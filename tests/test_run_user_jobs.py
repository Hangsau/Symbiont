"""
Tests for src/user_scheduler.py — cooldown & cron 邏輯（pure functions only）
"""
import time
import math
from datetime import datetime
from pathlib import Path

# src/ 是 package，直接 import
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.user_scheduler import should_run_job, _cron_is_due


# ── should_run_job ───────────────────────────────────────────────

class TestShouldRunJob:
    def test_zero_cooldown_always_runs(self, tmp_path):
        """cooldown_hours=0 → 永遠跑"""
        job = {"name": "test", "cooldown_hours": 0}
        assert should_run_job(job, tmp_path) is True

    def test_negative_cooldown_treated_as_zero(self, tmp_path):
        """負數 cooldown → 視為 0，永遠跑"""
        job = {"name": "test", "cooldown_hours": -1}
        assert should_run_job(job, tmp_path) is True

    def test_missing_ts_file_should_run(self, tmp_path):
        """ts 檔不存在 → first run，應跑"""
        job = {"name": "nonexistent", "cooldown_hours": 24}
        assert should_run_job(job, tmp_path) is True

    def test_corrupt_ts_should_run(self, tmp_path):
        """ts 內容非數字 → fail open，應跑"""
        job = {"name": "corrupt", "cooldown_hours": 24}
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "last_user_job_corrupt_ts.txt").write_text("not_a_number")
        assert should_run_job(job, data_dir) is True

    def test_nan_ts_should_run(self, tmp_path):
        """ts 是 nan（極罕見）→ fail open，應跑"""
        job = {"name": "nan_test", "cooldown_hours": 24}
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "last_user_job_nan_test_ts.txt").write_text("nan")
        assert should_run_job(job, data_dir) is True

    def test_inf_ts_should_run(self, tmp_path):
        """ts 是 inf → fail open，應跑"""
        job = {"name": "inf_test", "cooldown_hours": 24}
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "last_user_job_inf_test_ts.txt").write_text("inf")
        assert should_run_job(job, data_dir) is True

    def test_clock_backwards_should_run(self, tmp_path):
        """時鐘倒退（last > now_ts）→ fail open，應跑"""
        job = {"name": "test", "cooldown_hours": 24}
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        future_ts = 10000.0
        (data_dir / "last_user_job_test_ts.txt").write_text(str(future_ts))
        assert should_run_job(job, data_dir, now_ts=1000.0) is True

    def test_within_cooldown_should_skip(self, tmp_path):
        """1 小時前剛跑過 → 24h cooldown 內，不跑"""
        job = {"name": "test", "cooldown_hours": 24}
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        now = 10000.0
        last = now - 3600  # 1 小時前
        (data_dir / "last_user_job_test_ts.txt").write_text(str(last))
        assert should_run_job(job, data_dir, now_ts=now) is False

    def test_cooldown_elapsed_should_run(self, tmp_path):
        """25 小時前跑過 → 過 24h cooldown，應跑"""
        job = {"name": "test", "cooldown_hours": 24}
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        now = 10000.0
        last = now - (25 * 3600)  # 25 小時前
        (data_dir / "last_user_job_test_ts.txt").write_text(str(last))
        assert should_run_job(job, data_dir, now_ts=now) is True

    def test_exactly_at_cooldown_boundary_should_run(self, tmp_path):
        """剛好 cooldown 過期（>=）→ 應跑"""
        job = {"name": "test", "cooldown_hours": 24}
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        now = 10000.0
        last = now - (24 * 3600)  # 剛好 24 小時前
        (data_dir / "last_user_job_test_ts.txt").write_text(str(last))
        assert should_run_job(job, data_dir, now_ts=now) is True


# ── _cron_is_due ─────────────────────────────────────────────────

class TestCronIsDue:
    def test_wildcard_matches_any_datetime(self):
        """'* * * * *' 匹配任意時間"""
        dt = datetime(2026, 5, 4, 15, 30)
        assert _cron_is_due("* * * * *", dt) is True

    def test_exact_hour_match(self):
        """'0 4 * * *' 匹配 hour=4,min=0"""
        dt_match = datetime(2026, 5, 4, 4, 0)
        dt_no_match = datetime(2026, 5, 4, 5, 0)
        assert _cron_is_due("0 4 * * *", dt_match) is True
        assert _cron_is_due("0 4 * * *", dt_no_match) is False

    def test_exact_minute_match(self):
        """'30 * * * *' 匹配 min=30 任意小時"""
        dt_match = datetime(2026, 5, 4, 14, 30)
        dt_no_match = datetime(2026, 5, 4, 14, 31)
        assert _cron_is_due("30 * * * *", dt_match) is True
        assert _cron_is_due("30 * * * *", dt_no_match) is False

    def test_dom_match(self):
        """day-of-month 匹配"""
        dt_match = datetime(2026, 5, 15, 10, 0)
        dt_no_match = datetime(2026, 5, 14, 10, 0)
        assert _cron_is_due("0 10 15 * *", dt_match) is True
        assert _cron_is_due("0 10 15 * *", dt_no_match) is False

    def test_month_match(self):
        """month 匹配（1=January）"""
        dt_match = datetime(2026, 3, 1, 0, 0)
        dt_no_match = datetime(2026, 4, 1, 0, 0)
        assert _cron_is_due("0 0 1 3 *", dt_match) is True
        assert _cron_is_due("0 0 1 3 *", dt_no_match) is False

    def test_dow_match(self):
        """day-of-week 匹配（0=Monday, 6=Sunday）"""
        # 2026-05-04 是星期一（weekday()=0）
        dt_monday = datetime(2026, 5, 4, 0, 0)
        # 2026-05-05 是星期二（weekday()=1）
        dt_tuesday = datetime(2026, 5, 5, 0, 0)
        assert _cron_is_due("0 0 * * 0", dt_monday) is True
        assert _cron_is_due("0 0 * * 0", dt_tuesday) is False

    def test_invalid_field_count_returns_false(self):
        """欄位數錯誤（少於 5）→ False"""
        dt = datetime(2026, 5, 4, 10, 0)
        assert _cron_is_due("* * * *", dt) is False

    def test_invalid_numeric_field_returns_false(self):
        """非數字欄位 → False"""
        dt = datetime(2026, 5, 4, 10, 0)
        assert _cron_is_due("abc * * * *", dt) is False

    def test_extra_fields_returns_false(self):
        """超過 5 個欄位 → False"""
        dt = datetime(2026, 5, 4, 10, 0)
        assert _cron_is_due("0 10 * * * extra", dt) is False

    def test_minute_or_logic_in_first_field(self):
        """第一個欄位若提供具體值，應精確匹配（無 OR）"""
        # 根據實作的邏輯：matches(minute_f, ...) OR minute_f == "*"
        # 應該是 AND 邏輯
        dt = datetime(2026, 5, 4, 10, 5)
        assert _cron_is_due("5 10 * * *", dt) is True
        assert _cron_is_due("6 10 * * *", dt) is False
