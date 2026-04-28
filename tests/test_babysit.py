"""
Tests for src/babysit.py — pure functions only (no transport, no LLM calls)

注意：_parse_sentinel、lock 相關函式在 Phase 2 (A1+A2) 加入後才可正確運作。
"""
import json
import os
import time
import pytest
from pathlib import Path

from src.babysit import (
    _parse_sentinel,
    _acquire_lock,
    _release_lock,
    LOCK_FILE,
    TeachingState,
    NEEDS_HUMAN,
    NO_REPLY,
)


# ── _parse_sentinel ──────────────────────────────────────────────

class TestParseSentinel:
    def test_goal_achieved_exact(self):
        assert _parse_sentinel("GOAL_ACHIEVED") == "goal_achieved"

    def test_goal_achieved_with_whitespace(self):
        assert _parse_sentinel("  GOAL_ACHIEVED  ") == "goal_achieved"

    def test_goal_achieved_with_chinese_period(self):
        """LLM 可能在 sentinel 後加中文句號"""
        assert _parse_sentinel("GOAL_ACHIEVED。") == "goal_achieved"

    def test_goal_achieved_with_ascii_period(self):
        assert _parse_sentinel("GOAL_ACHIEVED.") == "goal_achieved"

    def test_goal_achieved_followed_by_explanation(self):
        """GOAL_ACHIEVED 後接說明文字（多行）仍應被識別"""
        assert _parse_sentinel("GOAL_ACHIEVED\n\n以下是教學總結...") == "goal_achieved"

    def test_needs_human_detected(self):
        assert _parse_sentinel(f"{NEEDS_HUMAN}: 需要人工確認") == "needs_human"

    def test_no_reply_detected(self):
        assert _parse_sentinel(NO_REPLY) == "no_reply"

    def test_regular_reply(self):
        assert _parse_sentinel("這是一個正常回應") == "reply"
        assert _parse_sentinel("請問你有想過...") == "reply"

    def test_empty_string_is_reply(self):
        """空字串不匹配任何 sentinel，回傳 reply"""
        assert _parse_sentinel("") == "reply"

    def test_goal_not_matched_in_body_only(self):
        """GOAL_ACHIEVED 在第二行不算 sentinel（避免誤觸）"""
        # 第一行是普通文字，GOAL_ACHIEVED 在第二行
        result = _parse_sentinel("首先讓我評估你的回應\nGOAL_ACHIEVED")
        assert result == "reply"


# ── _acquire_lock / _release_lock ────────────────────────────────

class TestLock:
    def _ensure_data_dir(self, base_dir: Path):
        (base_dir / "data").mkdir(parents=True, exist_ok=True)

    def test_acquire_success(self, tmp_path):
        self._ensure_data_dir(tmp_path)
        assert _acquire_lock(tmp_path) is True
        assert (tmp_path / LOCK_FILE).exists()
        _release_lock(tmp_path)

    def test_acquire_fails_when_already_locked(self, tmp_path):
        self._ensure_data_dir(tmp_path)
        _acquire_lock(tmp_path)
        assert _acquire_lock(tmp_path) is False
        _release_lock(tmp_path)

    def test_release_removes_lock(self, tmp_path):
        self._ensure_data_dir(tmp_path)
        _acquire_lock(tmp_path)
        _release_lock(tmp_path)
        assert not (tmp_path / LOCK_FILE).exists()

    def test_stale_lock_is_force_acquired(self, tmp_path):
        """mtime 超過 lock_max_age 的 lock 應被強制刪除並重新取得"""
        self._ensure_data_dir(tmp_path)
        lock = tmp_path / LOCK_FILE
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text("stale lock content")
        # 設 mtime 為 2000 秒前（遠超預設 900 秒上限）
        old_time = time.time() - 2000
        os.utime(lock, (old_time, old_time))

        assert _acquire_lock(tmp_path) is True
        _release_lock(tmp_path)


# ── TeachingState roundtrip ──────────────────────────────────────

class TestTeachingStateRoundtrip:
    def test_roundtrip_all_fields(self):
        ts = TeachingState(
            status="waiting_reply",
            goal="test goal",
            last_question="test question?",
            current_round=3,
            max_rounds=20,
            last_processed_dialogue="dialogue_123.json",
            last_sent_ts=1234567890.0,
            completed_at="",
            completion_summary="",
            timeout_warning_ts=0.0,
        )
        restored = TeachingState.from_dict(ts.to_dict())
        assert restored.status == ts.status
        assert restored.goal == ts.goal
        assert restored.last_question == ts.last_question
        assert restored.current_round == ts.current_round
        assert restored.max_rounds == ts.max_rounds
        assert restored.last_sent_ts == ts.last_sent_ts

    def test_roundtrip_defaults(self):
        ts = TeachingState()
        restored = TeachingState.from_dict(ts.to_dict())
        assert restored.status == "idle"
        assert restored.goal == ""
        assert restored.current_round == 1
        assert restored.max_rounds == 20
        assert restored.last_sent_ts == 0.0

    def test_completed_fields_only_in_dict_when_set(self):
        """completed_at 和 completion_summary 為空時不寫入 dict（節省空間）"""
        ts = TeachingState(status="idle")
        d = ts.to_dict()
        assert "completed_at" not in d
        assert "completion_summary" not in d

    def test_from_empty_dict_uses_defaults(self):
        ts = TeachingState.from_dict({})
        assert ts.status == "idle"
        assert ts.current_round == 1
