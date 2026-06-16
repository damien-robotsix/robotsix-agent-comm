"""Check mypy."""
import subprocess, sys
result = subprocess.run(
    [sys.executable, "-m", "mypy",
     "src/robotsix_agent_comm/transport/brokered.py",
     "src/robotsix_agent_comm/transport/__init__.py",
     "src/robotsix_agent_comm/sdk/agent.py"],
    capture_output=True, text=True, timeout=60
)
print(result.stdout)
if result.stderr:
    print("STDERR:", result.stderr)
print("Exit code:", result.returncode)
