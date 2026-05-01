"""
Tests for scripts/run_audit.py — cooldown 邏輯（pure functions only，不跑 subprocess）
"""
import time
import sys
from pathlib import Path

# scripts/ 不是 package，要直接載入
import importlib.util
ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "run_audit", ROOT / "scripts" / "run_audit.py"
)
run_audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_audit)


# ── should_run ───────────────────────────────────────────────────

class TestShouldRun:
    def test_no_ts_file_should_run(self, tmp_path):
        """ts 檔不存在 → first run，應跑"""
        assert run_audit.should_run(tmp_path / "nonexistent.txt", 24) is True

    def test_corrupt_ts_should_run(self, tmp_path):
        """ts 內容非數字 → fail open，應跑"""
        p = tmp_path / "ts.txt"
        p.write_text("not a number", encoding="utf-8")
        assert run_audit.should_run(p, 24) is True

    def test_empty_ts_should_run(self, tmp_path):
        p = tmp_path / "ts.txt"
        p.write_text("", encoding="utf-8")
        assert run_audit.should_run(p, 24) is True

    def test_future_ts_should_run(self, tmp_path):
        """時鐘倒退（ts 在未來）→ fail open，應跑"""
        p = tmp_path / "ts.txt"
        future = time.time() + 3600
        p.write_text(str(future), encoding="utf-8")
        assert run_audit.should_run(p, 24) is True

    def test_nan_ts_should_run(self, tmp_path):
        """ts 是 nan（極罕見）→ fail open，應跑（避免 cooldown 永久卡住）"""
        p = tmp_path / "ts.txt"
        p.write_text("nan", encoding="utf-8")
        assert run_audit.should_run(p, 24) is True

    def test_inf_ts_should_run(self, tmp_path):
        """ts 是 inf → fail open，應跑"""
        p = tmp_path / "ts.txt"
        p.write_text("inf", encoding="utf-8")
        assert run_audit.should_run(p, 24) is True

    def test_fresh_ts_should_skip(self, tmp_path):
        """1 小時前剛跑過 → 24h cooldown 內，不跑"""
        p = tmp_path / "ts.txt"
        p.write_text(str(time.time() - 3600), encoding="utf-8")
        assert run_audit.should_run(p, 24) is False

    def test_stale_ts_should_run(self, tmp_path):
        """25 小時前跑過 → 過 24h cooldown，應跑"""
        p = tmp_path / "ts.txt"
        p.write_text(str(time.time() - 25 * 3600), encoding="utf-8")
        assert run_audit.should_run(p, 24) is True

    def test_exactly_at_cooldown_boundary_should_run(self, tmp_path):
        """剛好 cooldown 過期（>=）→ 應跑"""
        p = tmp_path / "ts.txt"
        now = 10000.0
        p.write_text(str(now - 24 * 3600), encoding="utf-8")
        assert run_audit.should_run(p, 24, now_ts=now) is True

    def test_zero_cooldown_always_runs(self, tmp_path):
        """cooldown=0 → 永遠跑（debug 用）"""
        p = tmp_path / "ts.txt"
        p.write_text(str(time.time()), encoding="utf-8")  # 剛跑過
        assert run_audit.should_run(p, 0) is True

    def test_negative_cooldown_treated_as_zero(self, tmp_path):
        """負數 cooldown → 視為 0，永遠跑"""
        p = tmp_path / "ts.txt"
        p.write_text(str(time.time()), encoding="utf-8")
        assert run_audit.should_run(p, -1) is True


# ── read_cooldown_hours ──────────────────────────────────────────

class TestReadCooldownHours:
    def test_missing_config_uses_default(self, tmp_path):
        assert run_audit.read_cooldown_hours(tmp_path / "nonexistent.yaml") \
            == run_audit.DEFAULT_COOLDOWN_HOURS

    def test_corrupt_yaml_uses_default(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("not: valid: yaml: ::", encoding="utf-8")
        assert run_audit.read_cooldown_hours(p) == run_audit.DEFAULT_COOLDOWN_HOURS

    def test_missing_section_uses_default(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("other_section:\n  foo: bar\n", encoding="utf-8")
        assert run_audit.read_cooldown_hours(p) == run_audit.DEFAULT_COOLDOWN_HOURS

    def test_explicit_value_used(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("memory_audit:\n  audit_cooldown_hours: 12\n", encoding="utf-8")
        assert run_audit.read_cooldown_hours(p) == 12.0

    def test_negative_value_clamped_to_zero(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("memory_audit:\n  audit_cooldown_hours: -5\n", encoding="utf-8")
        assert run_audit.read_cooldown_hours(p) == 0.0

    def test_string_value_uses_default(self, tmp_path):
        p = tmp_path / "config.yaml"
        p.write_text("memory_audit:\n  audit_cooldown_hours: invalid\n",
                     encoding="utf-8")
        assert run_audit.read_cooldown_hours(p) == run_audit.DEFAULT_COOLDOWN_HOURS


# ── write_last_run ───────────────────────────────────────────────

class TestWriteLastRun:
    def test_writes_timestamp(self, tmp_path):
        p = tmp_path / "ts.txt"
        run_audit.write_last_run(p, now_ts=1234567890.5)
        assert float(p.read_text().strip()) == 1234567890.5

    def test_creates_parent_dir(self, tmp_path):
        p = tmp_path / "subdir" / "ts.txt"
        run_audit.write_last_run(p, now_ts=100.0)
        assert p.exists()
        assert float(p.read_text().strip()) == 100.0

    def test_write_failure_does_not_raise(self, tmp_path, monkeypatch, capsys):
        """寫失敗只 stderr 印 warning，不 raise"""
        def boom(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr(Path, "write_text", boom)
        # 不該拋例外
        run_audit.write_last_run(tmp_path / "ts.txt", now_ts=100.0)
        captured = capsys.readouterr()
        assert "write last_audit_ts failed" in captured.err
