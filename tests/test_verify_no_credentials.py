"""The post-build credential gate must actually scan the build it is given."""
import subprocess
import sys
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_no_credentials.py"


def _run(root: Path):
    return subprocess.run([sys.executable, str(TOOL), str(root)],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def test_clean_bundle_passes(tmp_path):
    (tmp_path / "WeReadWorker").mkdir()
    (tmp_path / "WeReadWorker" / "app.py").write_text("print('ok')", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "scanned 1 files" in result.stdout
    assert "CLEAN" in result.stdout


def test_credential_file_is_reported(tmp_path):
    (tmp_path / "session-cookies.json").write_text("{}", encoding="utf-8")
    result = _run(tmp_path)
    assert result.returncode == 1
    assert "FOUND:" in result.stdout and "REVIEW NEEDED" in result.stdout


def test_missing_bundle_fails_loudly_instead_of_passing(tmp_path):
    # The old default was release-qrlogin, which had been deleted: rglob scanned
    # nothing and the gate printed CLEAN. A missing root must be an error.
    result = _run(tmp_path / "does-not-exist")
    assert result.returncode == 2
    assert "BUNDLE NOT FOUND" in result.stdout
