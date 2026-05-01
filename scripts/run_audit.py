"""
run_audit.py — Task Scheduler wrapper for memory_audit.py (Windows, no window)

Task Scheduler 每小時觸發此腳本，內部 cooldown 控制實際執行頻率。
- cooldown_hours（預設 24）內已跑過 → sys.exit 0 不跑
- 超過 cooldown / first run / 時鐘倒退 → 跑 memory_audit.py，成功後寫 last_audit_ts.txt

設計考量：固定時間 trigger（如 ONLOGON 或 DAILY 04:00）對筆電/出差/Sleep
使用者不可靠（電腦不在開機 → 跳過 → 永遠等不到下次）。
HOURLY trigger + 內部 cooldown 對所有使用情境都能在開機後 1 小時內執行。

cooldown 邏輯抽成 pure function 方便單元測試（見 tests/test_run_audit.py）。
"""

import math
import os
import sys
import time
import subprocess
from pathlib import Path

DEFAULT_COOLDOWN_HOURS = 24
LAST_AUDIT_TS_FILENAME = "data/last_audit_ts.txt"


def read_cooldown_hours(config_path: Path) -> float:
    """讀 config.yaml 的 memory_audit.audit_cooldown_hours。
    缺欄位 / parse 失敗 → DEFAULT_COOLDOWN_HOURS。負數 → 0。
    """
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        v = cfg.get("memory_audit", {}).get("audit_cooldown_hours",
                                              DEFAULT_COOLDOWN_HOURS)
        return max(float(v), 0.0)
    except Exception:
        return float(DEFAULT_COOLDOWN_HOURS)


def should_run(ts_file: Path, cooldown_hours: float,
               now_ts: float | None = None) -> bool:
    """判斷是否該執行 memory_audit。fail open：任何異常都回 True（多跑無害）。

    - cooldown_hours <= 0 → True（永遠跑，debug 用）
    - ts 檔不存在 / 內容損壞 → True（first run / 修復狀態）
    - ts 在未來（時鐘倒退）→ True
    - 距上次跑 >= cooldown 小時 → True
    - 否則 → False
    """
    if cooldown_hours <= 0:
        return True
    if now_ts is None:
        now_ts = time.time()
    try:
        last = float(ts_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    if not math.isfinite(last):  # nan / inf → fail open
        return True
    delta = now_ts - last
    if delta < 0:  # 時鐘倒退
        return True
    return delta >= cooldown_hours * 3600


def write_last_run(ts_file: Path, now_ts: float | None = None) -> None:
    """寫 last_audit_ts.txt。寫失敗只 stderr 印 warning，不 raise
    （fail open：寫失敗下次仍會跑，最壞重複工作不會 silent dead）。
    """
    if now_ts is None:
        now_ts = time.time()
    try:
        ts_file.parent.mkdir(parents=True, exist_ok=True)
        ts_file.write_text(f"{now_ts}\n", encoding="utf-8")
    except OSError as e:
        print(f"[run_audit] write last_audit_ts failed: {e}", file=sys.stderr)


def main():
    agent_dir = Path(__file__).parent.parent
    log = agent_dir / "data" / "audit_hook.log"
    ts_file = agent_dir / LAST_AUDIT_TS_FILENAME
    config_path = agent_dir / "config.yaml"

    cooldown = read_cooldown_hours(config_path)
    if not should_run(ts_file, cooldown):
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

    if result.returncode == 0:
        write_last_run(ts_file)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
