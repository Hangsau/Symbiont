"""
run_evolve.py — Task Scheduler wrapper for evolve.py (Windows, no window)

Task Scheduler 每分鐘用 pythonw.exe 執行此腳本。
有 pending_evolve.txt 才跑 evolve.py，否則靜默退出。
"""

import os
import sys
import subprocess
from pathlib import Path

agent_dir = Path(__file__).parent.parent
pending   = agent_dir / "data" / "pending_evolve.txt"
log       = agent_dir / "data" / "evolve_hook.log"

if not pending.exists():
    sys.exit(0)

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
# 確保 claude CLI 可以被找到（npm global bin）
env["PATH"] = (
    r"C:\Users\\" + os.environ.get("USERNAME", "") + r"\AppData\Roaming\npm"
    + ";" + r"C:\Program Files\nodejs"
    + ";" + env.get("PATH", "")
)

python_exe = sys.executable.replace("pythonw.exe", "python.exe")

result = subprocess.run(
    [python_exe, "-u", "src/evolve.py", "--skip-if-wrap-done"],
    cwd=str(agent_dir),
    env=env,
    capture_output=True,
)
with open(log, "a", encoding="utf-8", errors="replace") as f:
    f.write(result.stdout.decode("utf-8", errors="replace"))
    f.write(result.stderr.decode("utf-8", errors="replace"))
