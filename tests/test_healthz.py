"""
Tests for src/healthz.py — heartbeat 評估邏輯（boundary 為主）
"""
import json
import time
import pytest

from src.healthz import load_heartbeat, evaluate, DEFAULT_MAX_AGE_SECONDS


# ── load_heartbeat ───────────────────────────────────────────────

class TestLoadHeartbeat:
    def test_missing_file_returns_none(self, tmp_path):
        assert load_heartbeat(tmp_path / "nonexistent.json") is None

    def test_corrupt_json_returns_none(self, tmp_path):
        p = tmp_path / "hb.json"
        p.write_text("not valid json{{{", encoding="utf-8")
        assert load_heartbeat(p) is None

    def test_valid_json_returns_dict(self, tmp_path):
        p = tmp_path / "hb.json"
        payload = {"last_run_ts": 123, "agents_pinged": {}}
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert load_heartbeat(p) == payload


# ── evaluate ─────────────────────────────────────────────────────

class TestEvaluate:
    def _make_hb(self, last_run_ago_s: float = 60, agents: dict = None):
        """快捷建 heartbeat dict。"""
        now = time.time()
        return {
            "schema_version": 1,
            "last_run_ts": now - last_run_ago_s,
            "last_run_duration_ms": 1234,
            "agents_pinged": agents if agents is not None
                              else {"talos": {"ssh_ok": True, "checked_at_ts": now - 60}},
        }

    def test_no_heartbeat_returns_unhealthy(self):
        healthy, msgs = evaluate(None, max_age_seconds=300)
        assert healthy is False
        assert any("不存在" in m or "損壞" in m for m in msgs)

    def test_missing_fields_returns_unhealthy(self):
        healthy, _ = evaluate({"schema_version": 1}, max_age_seconds=300)
        assert healthy is False

    def test_wrong_field_types_returns_unhealthy(self):
        bad = {"last_run_ts": "not a number", "agents_pinged": []}
        healthy, _ = evaluate(bad, max_age_seconds=300)
        assert healthy is False

    def test_stale_last_run_returns_unhealthy(self):
        hb = self._make_hb(last_run_ago_s=600)  # 10 min old
        healthy, msgs = evaluate(hb, max_age_seconds=300)
        assert healthy is False
        assert any("過期" in m for m in msgs)

    def test_any_ssh_fail_returns_unhealthy(self):
        hb = self._make_hb(agents={
            "talos": {"ssh_ok": True, "checked_at_ts": time.time()},
            "hestia": {"ssh_ok": False, "checked_at_ts": time.time()},
        })
        healthy, msgs = evaluate(hb, max_age_seconds=300)
        assert healthy is False
        assert any("FAIL" in m and "hestia" in m for m in msgs)

    def test_allow_partial_with_one_ok_is_healthy(self):
        hb = self._make_hb(agents={
            "talos": {"ssh_ok": True, "checked_at_ts": time.time()},
            "hestia": {"ssh_ok": False, "checked_at_ts": time.time()},
        })
        healthy, _ = evaluate(hb, max_age_seconds=300, allow_partial=True)
        assert healthy is True

    def test_allow_partial_all_fail_still_unhealthy(self):
        hb = self._make_hb(agents={
            "talos": {"ssh_ok": False, "checked_at_ts": time.time()},
        })
        healthy, _ = evaluate(hb, max_age_seconds=300, allow_partial=True)
        assert healthy is False

    def test_future_timestamp_is_healthy(self):
        """時鐘漂移：last_run_ts 在未來 → 不視為 stale"""
        now = time.time()
        hb = {
            "schema_version": 1,
            "last_run_ts": now + 60,  # 60 秒在未來
            "agents_pinged": {"talos": {"ssh_ok": True, "checked_at_ts": now}},
        }
        healthy, _ = evaluate(hb, max_age_seconds=300)
        assert healthy is True

    def test_empty_agents_with_fresh_run_is_healthy(self):
        hb = self._make_hb(agents={})
        healthy, msgs = evaluate(hb, max_age_seconds=300)
        assert healthy is True
        assert any("沒有 enabled agent" in m for m in msgs)

    def test_fresh_run_with_all_ssh_ok_is_healthy(self):
        hb = self._make_hb(last_run_ago_s=30)
        healthy, msgs = evaluate(hb, max_age_seconds=300)
        assert healthy is True
        assert any("OK" in m and "talos" in m for m in msgs)

    def test_default_max_age_is_300_seconds(self):
        """確認預設值符合規劃（2.5 倍 babysit 週期 + 容忍 1 次 lock skip）"""
        assert DEFAULT_MAX_AGE_SECONDS == 300
