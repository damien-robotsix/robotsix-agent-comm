"""Run the new integration tests."""
import subprocess, sys
result = subprocess.run(
    [sys.executable, "-m", "pytest",
     "tests/broker/test_brokered_integration.py",
     "-x", "-v", "--tb=short", "-q"],
    capture_output=True, text=True, timeout=120
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:2000])
print("Exit code:", result.returncode)
