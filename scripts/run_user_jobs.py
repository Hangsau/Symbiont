"""
run_user_jobs.py — Task Scheduler wrapper for user_scheduler.py (Windows, no window)

Task Scheduler 每小時觸發此腳本，user_scheduler.py 內部依 cron 表達式與 cooldown
判斷各 job 是否應執行。

設計：
  - user_jobs: [] 時直接 exit 0，無額外開銷
  - 所有輸出（stdout + stderr）append 到 data/user_jobs.log
"""

import os
import sys
import subprocess
from pathlib import Path


def main() -> None:
    agent_dir = Path(__file__).parent.parent
    log = agent_dir / "data" / "user_jobs.log"
    log.parent.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    python_exe = sys.executable.replace("pythonw.exe", "python.exe")

    result = subprocess.run(
        [python_exe, "-u", "src/user_scheduler.py"],
        cwd=str(agent_dir),
        env=env,
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    with open(log, "a", encoding="utf-8", errors="replace") as f:
        f.write(result.stdout.decode("utf-8", errors="replace"))
        f.write(result.stderr.decode("utf-8", errors="replace"))

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
