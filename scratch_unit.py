"""Run the new brokered unit tests."""

import subprocess
import sys

result = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/transport/test_brokered.py",
        "-x",
        "-v",
        "--tb=short",
        "-q",
    ],
    capture_output=True,
    text=True,
    timeout=120,
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:2000])
print("Exit code:", result.returncode)
