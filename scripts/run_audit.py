"""
run_audit.py — Task Scheduler wrapper for memory_audit.py (Windows, no window)

Task Scheduler 每日 04:00 用 pythonw.exe 執行此腳本，無條件跑 memory_audit.py。
（原本的 pending_audit.txt gate 已移除：ONLOGON trigger 在 Win11 fast startup
下幾乎永不觸發，改成 DAILY trigger 後不需要 hook 旗標補跑。
trigger-evolve.py 仍會寫 pending_audit.txt 但不再被讀取，無害。）
"""

import os
import sys
import subprocess
from pathlib import Path

agent_dir = Path(__file__).parent.parent
log       = agent_dir / "data" / "audit_hook.log"

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
