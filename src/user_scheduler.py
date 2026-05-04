"""
user_scheduler.py — 執行 config.yaml user_jobs 定義的用戶自訂排程任務

設計原則：
  - 與 Symbiont 自身維護 jobs（evolve/synthesize/memory_audit）完全獨立
  - HOURLY trigger + per-job cooldown（同 run_audit.py 模式）
  - pipeline 任一 step 失敗 → 後續不執行，cooldown 不寫（下次從 step 1 重試）
  - user_jobs parse 失敗 → log warning + return []，不影響 Symbiont 其他功能
"""

import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config
from src.utils.claude_runner import run_claude
from src.utils.file_ops import append_log

DEFAULT_COOLDOWN_HOURS = 24


# ── cron 解析 ─────────────────────────────────────────────────────

def _cron_is_due(cron_expr: str, now: datetime) -> bool:
    """
    判斷 cron 表達式在當前時間是否「到期」。
    精度：小時級（配合 HOURLY Task Scheduler trigger）。
    格式：5 欄位 "分 時 日 月 週"，支援 * 和數字，不支援 /step 或 range。
    """
    try:
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            return False
        minute_f, hour_f, dom_f, month_f, dow_f = parts

        def matches(field: str, value: int) -> bool:
            return field == "*" or int(field) == value

        return (matches(minute_f, now.minute) and matches(hour_f, now.hour)
                and matches(dom_f, now.day) and matches(month_f, now.month)
                and matches(dow_f, now.weekday()))
    except Exception:
        return False


# ── cooldown ──────────────────────────────────────────────────────

def _ts_file(job: dict, data_dir: Path) -> Path:
    name = job.get("name", "unnamed").replace(" ", "_")
    return data_dir / f"last_user_job_{name}_ts.txt"


def should_run_job(job: dict, data_dir: Path,
                   now_ts: float | None = None) -> bool:
    """fail-open：任何異常都回 True（多跑無害）。"""
    try:
        cooldown_hours = float(job.get("cooldown_hours", DEFAULT_COOLDOWN_HOURS))
    except (TypeError, ValueError):
        cooldown_hours = float(DEFAULT_COOLDOWN_HOURS)
    if cooldown_hours <= 0:
        return True
    if now_ts is None:
        now_ts = time.time()
    try:
        last = float(_ts_file(job, data_dir).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    if not math.isfinite(last):
        return True
    delta = now_ts - last
    if delta < 0:
        return True
    return delta >= cooldown_hours * 3600


def write_job_last_run(job: dict, data_dir: Path,
                       now_ts: float | None = None) -> None:
    if now_ts is None:
        now_ts = time.time()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _ts_file(job, data_dir).write_text(f"{now_ts}\n", encoding="utf-8")
    except OSError as e:
        print(f"[user_scheduler] write cooldown failed for '{job.get('name')}': {e}",
              file=sys.stderr)


# ── job 執行 ──────────────────────────────────────────────────────

def _run_step(prompt: str, cwd: str | None, cfg: dict, log: Path) -> bool:
    """執行單一 step。成功回 True，失敗記 log 回 False。"""
    ts = datetime.now().isoformat(timespec="seconds")
    append_log(log, f"[{ts}] running step cwd={cwd} prompt={prompt[:80]!r}...")
    result = run_claude(prompt, cfg=cfg, cwd=cwd)
    if result is None:
        append_log(log, f"[{ts}] step FAILED (run_claude returned None)")
        return False
    append_log(log, f"[{ts}] step OK, output_len={len(result)}")
    return True


def run_job(job: dict, cfg: dict, log: Path) -> bool:
    """
    執行一個 job（simple 或 pipeline）。
    回傳 True = 全部完成；False = 失敗（cooldown 不應寫入）。
    """
    name = job.get("name", "unnamed")
    job_type = job.get("type", "simple")
    cwd = job.get("cwd") or None

    ts = datetime.now().isoformat(timespec="seconds")
    append_log(log, f"[{ts}] starting job '{name}' type={job_type}")

    if job_type == "pipeline":
        steps = job.get("steps", [])
        if not steps:
            append_log(log, f"[{ts}] job '{name}' has empty steps, treating as success")
            return True
        for i, step in enumerate(steps, 1):
            prompt = step.get("prompt", "")
            step_cwd = step.get("cwd") or cwd
            append_log(log, f"[{ts}] job '{name}' step {i}/{len(steps)}")
            if not _run_step(prompt, step_cwd, cfg, log):
                append_log(log, f"[{ts}] job '{name}' ABORTED at step {i}")
                return False
        return True

    # simple
    prompt = job.get("prompt", "")
    return _run_step(prompt, cwd, cfg, log)


# ── config 解析 ───────────────────────────────────────────────────

def load_user_jobs(config_path: Path) -> list[dict]:
    """
    讀 config.yaml 的 user_jobs 區塊。
    parse 失敗 → stderr warning + return []，不影響 Symbiont 其他功能。
    """
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        jobs = cfg.get("user_jobs", [])
        if not isinstance(jobs, list):
            print("[user_scheduler] user_jobs must be a list, got "
                  f"{type(jobs).__name__}, skipping", file=sys.stderr)
            return []
        return jobs
    except Exception as e:
        print(f"[user_scheduler] failed to parse user_jobs from config: {e}",
              file=sys.stderr)
        return []


# ── main ──────────────────────────────────────────────────────────

def main() -> None:
    agent_dir = Path(__file__).parent.parent
    config_path = agent_dir / "config.yaml"
    data_dir = agent_dir / "data"
    log = data_dir / "user_jobs.log"

    cfg = load_config()
    jobs = load_user_jobs(config_path)

    if not jobs:
        sys.exit(0)

    now_utc = datetime.now(timezone.utc)
    now_ts = time.time()

    for job in jobs:
        if not job.get("enabled", True):
            continue

        name = job.get("name", "unnamed")
        cron = job.get("cron", "")

        if cron and not _cron_is_due(cron, now_utc):
            continue

        if not should_run_job(job, data_dir, now_ts=now_ts):
            ts = datetime.now().isoformat(timespec="seconds")
            append_log(log, f"[{ts}] job '{name}' skipped (cooldown not elapsed)")
            continue

        success = run_job(job, cfg, log)
        if success:
            write_job_last_run(job, data_dir, now_ts=now_ts)


if __name__ == "__main__":
    main()
