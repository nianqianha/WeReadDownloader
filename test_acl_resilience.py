# -*- coding: utf-8 -*-
"""ACL hardening must never abort a download: unit-level proof."""
import sys, io, os, subprocess
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, r"C:\Users\asus\Desktop\各类\Kindle\05_补丁与其他\桌面端WeRead下载器")

import weread_desktop as wd
from pathlib import Path

cases = []

# 1. whoami itself blowing up
real_check_output = subprocess.check_output
def boom_check_output(*a, **k):
    raise OSError("simulated whoami failure")
subprocess.check_output = boom_check_output
try:
    wd._set_private_acl(Path(os.getcwd()), True)
    cases.append(("whoami failure is survived", True))
except Exception as exc:
    cases.append(("whoami failure is survived (%s)" % exc, False))
finally:
    subprocess.check_output = real_check_output

# 2. icacls returning non-zero
real_run = subprocess.run
def fail_run(*a, **k):
    class R:
        returncode = 5
        stdout = ""
        stderr = ""
    return R()
subprocess.run = fail_run
try:
    wd._set_private_acl(Path(os.getcwd()), True)
    cases.append(("icacls exit 5 is survived", True))
except Exception as exc:
    cases.append(("icacls exit 5 is survived (%s)" % exc, False))
finally:
    subprocess.run = real_run

# 3. icacls missing entirely
def missing_run(*a, **k):
    raise FileNotFoundError("icacls not found")
subprocess.run = missing_run
try:
    wd._set_private_acl(Path(os.getcwd()), False)
    cases.append(("missing icacls is survived", True))
except Exception as exc:
    cases.append(("missing icacls is survived (%s)" % exc, False))
finally:
    subprocess.run = real_run

# 4. the happy path still works and still really restricts
import tempfile
with tempfile.TemporaryDirectory() as tmp:
    target = Path(tmp) / "acl_target"
    target.mkdir()
    wd._set_private_acl(target, True)
    # icacls writes the account name in the console codepage (GBK on this box),
    # not UTF-8, so compare on the SID-stable tail of the ACE instead of the
    # literal account string.
    p = subprocess.run(["icacls", str(target)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = p.stdout
    cases.append(("happy path still grants the current user",
                  ":(OI)(CI)(F)" in out and "3320489737" in out))
    # exactly one ACE line before the summary, i.e. inheritance was dropped
    aces = [l for l in out.splitlines() if ":" in l and "(" in l]
    cases.append(("happy path drops inherited entries", len(aces) == 1))

# 5. secure_output_directory still creates the dir and still returns
with tempfile.TemporaryDirectory() as tmp:
    nested = Path(tmp) / "a" / "b" / "c"
    subprocess.run = fail_run
    try:
        wd.secure_output_directory(nested)
        cases.append(("secure_output_directory survives a failing ACL", nested.is_dir()))
    except Exception as exc:
        cases.append(("secure_output_directory survives a failing ACL (%s)" % exc, False))
    finally:
        subprocess.run = real_run

ok = True
for label, passed in cases:
    print(("  OK   " if passed else "  FAIL ") + label)
    ok = ok and passed
print()
print("ACL RESILIENCE:", "PASS" if ok else "FAIL")