"""Run all existing (pre-brokered) tests to ensure no regressions."""
import subprocess, sys

# Run the subset that existed before this ticket
result = subprocess.run(
    [sys.executable, "-m", "pytest",
     "tests/transport/",
     "tests/sdk/",
     "tests/broker/test_broker_integration.py",
     "tests/broker/test_server.py",
     "-x", "-v", "--tb=short", "-q"],
    capture_output=True, text=True, timeout=120
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[:2000])
print("Exit code:", result.returncode)
