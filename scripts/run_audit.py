"""
run_audit.py — Task Scheduler wrapper for memory_audit.py (Windows, no window)

Task Scheduler 登入後用 pythonw.exe 執行此腳本。
有 pending_audit.txt 才跑 memory_audit.py，否則靜默退出。
"""

import os
import sys
import subprocess
from pathlib import Path

agent_dir = Path(__file__).parent.parent
pending   = agent_dir / "data" / "pending_audit.txt"
log       = agent_dir / "data" / "audit_hook.log"

if not pending.exists():
    sys.exit(0)

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"

python_exe = sys.executable.replace("pythonw.exe", "python.exe")

result = subprocess.run(
    [python_exe, "-u", "src/memory_audit.py"],
    cwd=str(agent_dir),
    env=env,
    capture_output=True,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
with open(log, "a", encoding="utf-8", errors="replace") as f:
    f.write(result.stdout.decode("utf-8", errors="replace"))
    f.write(result.stderr.decode("utf-8", errors="replace"))
