"""
run_babysit.py — Task Scheduler wrapper for babysit.py (Windows, no window)

Task Scheduler 每 2 分鐘用 pythonw.exe 執行此腳本。
"""

import os
import sys
import subprocess
from pathlib import Path

agent_dir = Path(__file__).parent.parent
log       = agent_dir / "data" / "babysit_hook.log"

env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
env["PYTHONUTF8"] = "1"
env["PATH"] = (
    r"C:\Users\\" + os.environ.get("USERNAME", "") + r"\AppData\Roaming\npm"
    + ";" + r"C:\Program Files\nodejs"
    + ";" + env.get("PATH", "")
)

python_exe = sys.executable.replace("pythonw.exe", "python.exe")

result = subprocess.run(
    [python_exe, "-u", "src/babysit.py"],
    cwd=str(agent_dir),
    env=env,
    capture_output=True,
    creationflags=subprocess.CREATE_NO_WINDOW,
)
with open(log, "a", encoding="utf-8", errors="replace") as f:
    f.write(result.stdout.decode("utf-8", errors="replace"))
    f.write(result.stderr.decode("utf-8", errors="replace"))
