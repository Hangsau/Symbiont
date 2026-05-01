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
    _parse_mode,
    _strip_mode_line,
    _mark_processed,
    _is_state_fresh,
    _build_inbox_prompt,
    _build_discussion_prompt,
    _build_teaching_prompt,
    _acquire_lock,
    _release_lock,
    LOCK_FILE,
    TeachingState,
    AgentState,
    NEEDS_HUMAN,
    NO_REPLY,
    DEFAULT_MODE,
    VALID_MODES,
    MAX_PROCESSED_INBOX_HISTORY,
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

    def test_no_reply_with_punctuation(self):
        """修 #3：LLM 在 NO_REPLY_NEEDED 後加標點，仍應解析為 no_reply"""
        assert _parse_sentinel("NO_REPLY_NEEDED.") == "no_reply"
        assert _parse_sentinel("NO_REPLY_NEEDED。") == "no_reply"
        assert _parse_sentinel("NO_REPLY_NEEDED：純報告") == "no_reply"

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

    def test_legacy_dict_without_mode_defaults_to_teaching(self):
        """向後相容：舊 talos.json 沒有 mode 欄位 → 預設 teaching"""
        legacy = {
            "status": "idle",
            "goal": "",
            "last_question": "",
            "current_round": 1,
            "max_rounds": 20,
            "last_processed_dialogue": "",
            "last_sent_ts": 0.0,
        }
        ts = TeachingState.from_dict(legacy)
        assert ts.mode == DEFAULT_MODE == "teaching"

    def test_invalid_mode_falls_back_to_default(self):
        """未知 mode 字串 → fallback default"""
        ts = TeachingState.from_dict({"mode": "weird_mode"})
        assert ts.mode == DEFAULT_MODE

    def test_mode_roundtrip(self):
        for m in VALID_MODES:
            ts = TeachingState(mode=m)
            assert TeachingState.from_dict(ts.to_dict()).mode == m


# ── _parse_mode / _strip_mode_line ────────────────────────────────

class TestParseMode:
    def test_teaching_label(self):
        assert _parse_mode("MODE: teaching\n\n問句...") == "teaching"

    def test_discussion_label(self):
        assert _parse_mode("MODE: discussion\n\n回應...") == "discussion"

    def test_case_insensitive(self):
        assert _parse_mode("mode: TEACHING\n\n...") == "teaching"

    def test_with_markdown_bold(self):
        """LLM 可能輸出 **MODE**: teaching"""
        assert _parse_mode("**MODE**: teaching\n\n...") == "teaching"

    def test_with_markdown_bold_for_discussion(self):
        """避免 fallback false positive：discussion 必須真的被解析出來"""
        assert _parse_mode("**MODE**: discussion\n\n...") == "discussion"

    def test_unknown_mode_falls_back(self):
        assert _parse_mode("MODE: chitchat\n\n...") == DEFAULT_MODE

    def test_no_mode_label_falls_back(self):
        assert _parse_mode("這是普通回應") == DEFAULT_MODE

    def test_empty_response_falls_back(self):
        assert _parse_mode("") == DEFAULT_MODE


class TestStripModeLine:
    def test_strip_with_blank_line(self):
        s = "MODE: teaching\n\n這是內容"
        assert _strip_mode_line(s) == "這是內容"

    def test_strip_without_blank_line(self):
        s = "MODE: discussion\n直接接內容"
        assert _strip_mode_line(s) == "直接接內容"

    def test_no_mode_returns_original(self):
        s = "這是普通回應\n第二行"
        assert _strip_mode_line(s) == s

    def test_strip_markdown_mode(self):
        s = "**MODE**: teaching\n\n內容"
        assert _strip_mode_line(s) == "內容"


# ── _mark_processed ──────────────────────────────────────────────

class TestMarkProcessed:
    def test_appends_in_order(self):
        st = AgentState(processed_inbox=["a", "b"])
        _mark_processed(st, "c")
        assert st.processed_inbox == ["a", "b", "c"]

    def test_dedupes_existing(self):
        """重複 item 保留首次出現位置（首次處理時間順序）"""
        st = AgentState(processed_inbox=["a", "b", "c"])
        _mark_processed(st, "b")
        assert st.processed_inbox == ["a", "b", "c"]

    def test_truncates_to_history_limit(self):
        old = [f"f{i}" for i in range(MAX_PROCESSED_INBOX_HISTORY + 50)]
        st = AgentState(processed_inbox=old)
        _mark_processed(st, "new")
        assert len(st.processed_inbox) == MAX_PROCESSED_INBOX_HISTORY
        assert st.processed_inbox[-1] == "new"
        # 確認最後 N 個是按順序保留的（修 #2：不再隨機）
        assert st.processed_inbox == (old + ["new"])[-MAX_PROCESSED_INBOX_HISTORY:]


# ── _is_state_fresh ──────────────────────────────────────────────

class TestIsStateFresh:
    def test_zero_ts_not_fresh(self):
        ts = TeachingState(last_sent_ts=0.0)
        assert _is_state_fresh(ts, teaching_timeout=1800) is False

    def test_recent_ts_is_fresh(self):
        ts = TeachingState(last_sent_ts=time.time() - 60)
        assert _is_state_fresh(ts, teaching_timeout=1800) is True

    def test_stale_ts_not_fresh(self):
        ts = TeachingState(last_sent_ts=time.time() - 3600)
        assert _is_state_fresh(ts, teaching_timeout=1800) is False


# ── prompt builders 對 mode 的反應 ────────────────────────────────

class TestPromptModeRouting:
    def test_inbox_prompt_skips_stale_state(self):
        """修 #6：waiting_reply 但 last_sent_ts 過舊 → 不帶 ts_summary"""
        ts = TeachingState(
            status="waiting_reply",
            mode="teaching",
            goal="some goal",
            last_question="old question",
            last_sent_ts=time.time() - 3600,  # 1 小時前
        )
        prompt = _build_inbox_prompt(
            "talos", "system ctx", "new msg", ts, teaching_timeout=1800
        )
        assert "old question" not in prompt
        assert "教學目標" not in prompt

    def test_inbox_prompt_uses_fresh_teaching_state(self):
        ts = TeachingState(
            status="waiting_reply",
            mode="teaching",
            goal="learn X",
            last_question="how about Y?",
            last_sent_ts=time.time() - 60,
        )
        prompt = _build_inbox_prompt(
            "talos", "system ctx", "new msg", ts, teaching_timeout=1800
        )
        assert "learn X" in prompt
        assert "how about Y?" in prompt

    def test_inbox_prompt_uses_fresh_discussion_state(self):
        """discussion mode 不帶教學目標，只帶上一輪發言"""
        ts = TeachingState(
            status="waiting_reply",
            mode="discussion",
            goal="",
            last_question="my prior point",
            last_sent_ts=time.time() - 60,
        )
        prompt = _build_inbox_prompt(
            "talos", "system ctx", "new msg", ts, teaching_timeout=1800
        )
        assert "my prior point" in prompt
        assert "教學目標" not in prompt

    def test_discussion_prompt_has_no_socratic_instruction(self):
        """discussion prompt 不該強迫引導式問句"""
        ts = TeachingState(mode="discussion", current_round=2, max_rounds=20,
                            last_question="prev")
        prompt = _build_discussion_prompt("talos", "ctx", "agent reply", ts)
        assert "蘇格拉底" not in prompt
        assert "一次只問一件事" not in prompt
        assert "平等對話" in prompt
        assert NO_REPLY in prompt  # 結束 sentinel

    def test_teaching_prompt_has_socratic_instruction(self):
        ts = TeachingState(mode="teaching", current_round=2, max_rounds=20,
                            last_question="prev", goal="g")
        prompt = _build_teaching_prompt("talos", "ctx", "agent reply", ts)
        assert "一次只問一件事" in prompt
        assert "GOAL_ACHIEVED" in prompt
