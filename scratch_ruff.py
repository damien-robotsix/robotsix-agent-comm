"""Check style with ruff."""

import subprocess
import sys

result = subprocess.run(
    [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "src/robotsix_agent_comm/transport/brokered.py",
        "tests/transport/test_brokered.py",
        "tests/broker/test_brokered_integration.py",
    ],
    capture_output=True,
    text=True,
    timeout=30,
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
print("Exit code:", result.returncode)
print("---")
result2 = subprocess.run(
    [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "src/robotsix_agent_comm/transport/__init__.py",
        "src/robotsix_agent_comm/sdk/agent.py",
    ],
    capture_output=True,
    text=True,
    timeout=30,
)
print(result2.stdout)
if result2.stderr:
    print("STDERR:", result2.stderr)
print("Exit code:", result2.returncode)
