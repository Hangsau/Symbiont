"""
Tests for synthesize.py — staged commit idempotency and failure recovery

Focus: verify that synthesize.run() preserves intermediate state when failing mid-stage,
and resumes correctly without re-executing completed phases.

Scenario: patterns → memories → distill → prune → log stages.
Middle-stage injection (distill failure) should leave state intact for resume.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src import synthesize
from src.utils.file_ops import FileLock


class TestDistillFailurePreservesState:
    """test_distill_failure_preserves_state (必要)

    Mock _distill_memories to raise RuntimeError on first call.
    Verify that run() returns 1 and state is preserved with:
      - current_run_id not None
      - patterns_done_at == current_run_id
      - memories_done_at == current_run_id
      - distill_done_at is None
    """

    def test_distill_failure_preserves_state(self, tmp_path):
        """Failure in distill phase should preserve state for resume."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        state_path = data_dir / "synth_state.json"
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Create one fake session
        session_path = sessions_dir / "session-001.jsonl"
        session_path.write_text('{"type": "user"}\n')

        # Prepare fake config
        cfg = {
            "_root": str(tmp_path),
            "sessions_dir": str(sessions_dir),
            "data_dir": str(data_dir),
            "memory_dir": str(memory_dir),
            "memory_index": str(memory_dir / "MEMORY.md"),
            "error_log": str(tmp_path / "error.log"),
            "evolution_log": str(tmp_path / "evolution.log"),
            "primary_project_dir": str(tmp_path),
            "knowledge": {"enabled": "false"},
            "synthesize": {
                "sessions_per_cycle": 10,
                "ctx_cap_chars": 5000,
                "friction_per_session": 1000,
                "habit_per_session": 500,
                "min_evidence_sessions": 2,
            }
        }

        fake_synthesis_output = {
            "patterns": [],
            "memories": [
                {
                    "filename": "test_memory.md",
                    "content": "---\nname: test\ndescription: test\ntype: feedback\ncreated: 2026-04-30\n---\n\ntest content"
                }
            ],
            "synthesis_summary": "test run"
        }

        def mock_get_path(cfg, *args, **kwargs):
            key = args[0] if args else None
            if key == "primary_project_dir":
                return Path(cfg.get("primary_project_dir"))
            return cfg.get(key)

        with patch("src.synthesize.load_config", return_value=cfg), \
             patch("src.synthesize.check_auth", return_value=True), \
             patch("src.synthesize.get_path", side_effect=mock_get_path), \
             patch("src.synthesize.get_int", side_effect=lambda cfg, *args, **kwargs: cfg.get(args[0], {}).get(args[-1], kwargs.get("default", 0)) if isinstance(cfg.get(args[0]), dict) else kwargs.get("default", 0)), \
             patch("src.synthesize.get_str", side_effect=lambda cfg, *args, **kwargs: kwargs.get("default", "")), \
             patch("src.synthesize.find_sessions_after", return_value=[session_path]), \
             patch("src.synthesize.run_claude", return_value=json.dumps(fake_synthesis_output)), \
             patch("src.synthesize._load_existing_skill_descriptions", return_value=""), \
             patch("src.synthesize._write_skill", return_value=True), \
             patch("src.synthesize._update_skill_stats", return_value=[]), \
             patch("src.synthesize._write_memories", return_value=None), \
             patch("src.synthesize._distill_memories", side_effect=RuntimeError("distill failed")), \
             patch("src.synthesize.FileLock") as mock_lock:

            # Setup FileLock mock to be a context manager
            mock_lock.return_value.__enter__ = MagicMock(return_value=None)
            mock_lock.return_value.__exit__ = MagicMock(return_value=None)

            # First run: should fail at distill phase
            result = synthesize.run(dry_run=False)
            assert result == 1, "Expected run() to return 1 on failure"

            # Read state
            state = synthesize._load_synth_state(state_path)
            assert state["current_run_id"] is not None, "current_run_id should be set"
            assert state["patterns_done_at"] == state["current_run_id"], "patterns_done_at should equal current_run_id"
            assert state["memories_done_at"] == state["current_run_id"], "memories_done_at should equal current_run_id"
            assert state["distill_done_at"] is None, "distill_done_at should be None (not reached)"


class TestResumeSkipsCompletedPhases:
    """test_resume_skips_completed_phases (必要)

    Pre-setup state with completed patterns/memories phases.
    Verify that run_claude is NOT called (patterns skipped).
    Verify that distill/prune/log phases execute.
    After completion, current_run_id should be None.
    """

    def test_resume_skips_completed_phases(self, tmp_path):
        """Resume should skip already-completed phases."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        state_path = data_dir / "synth_state.json"
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Create one fake session
        session_path = sessions_dir / "session-001.jsonl"
        session_path.write_text('{"type": "user"}\n')

        # Pre-setup state: patterns and memories done
        run_id = "2026-04-30T10:00:00"
        initial_state = synthesize._default_synth_state_v2()
        initial_state.update({
            "current_run_id": run_id,
            "current_run_sessions": ["session-001"],
            "patterns_done_at": run_id,
            "memories_done_at": run_id,
            "distill_done_at": None,
            "prune_done_at": None,
            "log_done_at": None,
        })
        state_path.write_text(json.dumps(initial_state, indent=2))

        cfg = {
            "_root": str(tmp_path),
            "sessions_dir": str(sessions_dir),
            "data_dir": str(data_dir),
            "memory_dir": str(memory_dir),
            "memory_index": str(memory_dir / "MEMORY.md"),
            "error_log": str(tmp_path / "error.log"),
            "evolution_log": str(tmp_path / "evolution.log"),
            "primary_project_dir": str(tmp_path),
            "knowledge": {"enabled": "false"},
            "synthesize": {
                "sessions_per_cycle": 10,
                "ctx_cap_chars": 5000,
                "friction_per_session": 1000,
                "habit_per_session": 500,
                "min_evidence_sessions": 2,
            }
        }

        run_claude_calls = []

        def track_run_claude(prompt, cfg):
            run_claude_calls.append(prompt)
            return json.dumps({
                "patterns": [],
                "memories": [],
                "synthesis_summary": "test"
            })

        def mock_get_path(cfg, *args, **kwargs):
            key = args[0] if args else None
            if key == "primary_project_dir":
                return Path(cfg.get("primary_project_dir"))
            return cfg.get(key)

        with patch("src.synthesize.load_config", return_value=cfg), \
             patch("src.synthesize.check_auth", return_value=True), \
             patch("src.synthesize.get_path", side_effect=mock_get_path), \
             patch("src.synthesize.get_int", side_effect=lambda cfg, *args, **kwargs: cfg.get(args[0], {}).get(args[-1], kwargs.get("default", 0)) if isinstance(cfg.get(args[0]), dict) else kwargs.get("default", 0)), \
             patch("src.synthesize.get_str", side_effect=lambda cfg, *args, **kwargs: kwargs.get("default", "")), \
             patch("src.synthesize.find_session_by_uuid", return_value=session_path), \
             patch("src.synthesize.run_claude", side_effect=track_run_claude), \
             patch("src.synthesize._write_memories", return_value=None), \
             patch("src.synthesize._distill_memories", return_value={}), \
             patch("src.synthesize._run_update_knowledge_tags", return_value=None), \
             patch("src.synthesize._prune_memory_index", return_value=None), \
             patch("src.synthesize._append_evolution_log", return_value=None), \
             patch("src.synthesize.FileLock") as mock_lock:

            mock_lock.return_value.__enter__ = MagicMock(return_value=None)
            mock_lock.return_value.__exit__ = MagicMock(return_value=None)

            # Second run: resume and complete remaining phases
            result = synthesize.run(dry_run=False)
            assert result == 0, f"Expected run() to return 0 on success, got {result}"

            # run_claude should NOT have been called (patterns phase skipped)
            assert len(run_claude_calls) == 0, "run_claude should not be called (patterns phase skipped)"

            # Verify state is cleaned up
            state = synthesize._load_synth_state(state_path)
            assert state["current_run_id"] is None, "current_run_id should be None after completion"
            assert state["patterns_done_at"] is None, "patterns_done_at should be None after completion"
            assert state["distill_done_at"] is None, "distill_done_at should be None after completion"


class TestNoSessionsClearsRunId:
    """test_no_sessions_clears_run_id (建議加)

    When find_sessions_after returns empty list, run_id should be cleared.
    """

    def test_no_sessions_clears_run_id(self, tmp_path):
        """When no new sessions, run_id should be cleared."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        state_path = data_dir / "synth_state.json"
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        cfg = {
            "_root": str(tmp_path),
            "sessions_dir": str(sessions_dir),
            "data_dir": str(data_dir),
            "memory_dir": str(tmp_path / "memory"),
            "memory_index": str(tmp_path / "memory" / "MEMORY.md"),
            "error_log": str(tmp_path / "error.log"),
            "evolution_log": str(tmp_path / "evolution.log"),
            "primary_project_dir": str(tmp_path),
            "knowledge": {"enabled": "false"},
            "synthesize": {
                "sessions_per_cycle": 10,
                "ctx_cap_chars": 5000,
                "friction_per_session": 1000,
                "habit_per_session": 500,
                "min_evidence_sessions": 2,
            }
        }

        def mock_get_path(cfg, *args, **kwargs):
            key = args[0] if args else None
            if key == "primary_project_dir":
                return Path(cfg.get("primary_project_dir"))
            return cfg.get(key)

        with patch("src.synthesize.load_config", return_value=cfg), \
             patch("src.synthesize.check_auth", return_value=True), \
             patch("src.synthesize.get_path", side_effect=mock_get_path), \
             patch("src.synthesize.get_int", side_effect=lambda cfg, *args, **kwargs: cfg.get(args[0], {}).get(args[-1], kwargs.get("default", 0)) if isinstance(cfg.get(args[0]), dict) else kwargs.get("default", 0)), \
             patch("src.synthesize.get_str", side_effect=lambda cfg, *args, **kwargs: kwargs.get("default", "")), \
             patch("src.synthesize.find_sessions_after", return_value=[]):  # Empty: no new sessions

            result = synthesize.run(dry_run=False)
            assert result == 0, "Expected return 0 when no sessions"

            state = synthesize._load_synth_state(state_path)
            assert state["current_run_id"] is None, "current_run_id should be None when no sessions"


class TestRunIdClearedAfterCompletion:
    """test_run_id_cleared_after_completion (建議加)

    After all phases complete successfully, current_run_id should be None
    and cursor should be updated to last session.
    """

    def test_run_id_cleared_after_completion(self, tmp_path):
        """After successful completion, run_id should be cleared and cursor updated."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        state_path = data_dir / "synth_state.json"
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Create one fake session with specific mtime
        session_path = sessions_dir / "session-001.jsonl"
        session_path.write_text('{"type": "user"}\n')
        import os
        mtime = 1700000000.0
        os.utime(str(session_path), (mtime, mtime))

        cfg = {
            "_root": str(tmp_path),
            "sessions_dir": str(sessions_dir),
            "data_dir": str(data_dir),
            "memory_dir": str(memory_dir),
            "memory_index": str(memory_dir / "MEMORY.md"),
            "error_log": str(tmp_path / "error.log"),
            "evolution_log": str(tmp_path / "evolution.log"),
            "primary_project_dir": str(tmp_path),
            "knowledge": {"enabled": "false"},
            "synthesize": {
                "sessions_per_cycle": 10,
                "ctx_cap_chars": 5000,
                "friction_per_session": 1000,
                "habit_per_session": 500,
                "min_evidence_sessions": 2,
            }
        }

        fake_synthesis_output = {
            "patterns": [],
            "memories": [],
            "synthesis_summary": "test run"
        }

        def mock_get_path(cfg, *args, **kwargs):
            key = args[0] if args else None
            if key == "primary_project_dir":
                return Path(cfg.get("primary_project_dir"))
            return cfg.get(key)

        with patch("src.synthesize.load_config", return_value=cfg), \
             patch("src.synthesize.check_auth", return_value=True), \
             patch("src.synthesize.get_path", side_effect=mock_get_path), \
             patch("src.synthesize.get_int", side_effect=lambda cfg, *args, **kwargs: cfg.get(args[0], {}).get(args[-1], kwargs.get("default", 0)) if isinstance(cfg.get(args[0]), dict) else kwargs.get("default", 0)), \
             patch("src.synthesize.get_str", side_effect=lambda cfg, *args, **kwargs: kwargs.get("default", "")), \
             patch("src.synthesize.find_sessions_after", return_value=[session_path]), \
             patch("src.synthesize.run_claude", return_value=json.dumps(fake_synthesis_output)), \
             patch("src.synthesize._load_existing_skill_descriptions", return_value=""), \
             patch("src.synthesize._write_skill", return_value=True), \
             patch("src.synthesize._update_skill_stats", return_value=[]), \
             patch("src.synthesize._write_memories", return_value=None), \
             patch("src.synthesize._distill_memories", return_value={}), \
             patch("src.synthesize._run_update_knowledge_tags", return_value=None), \
             patch("src.synthesize._prune_memory_index", return_value=None), \
             patch("src.synthesize._append_evolution_log", return_value=None), \
             patch("src.synthesize.FileLock") as mock_lock:

            mock_lock.return_value.__enter__ = MagicMock(return_value=None)
            mock_lock.return_value.__exit__ = MagicMock(return_value=None)

            result = synthesize.run(dry_run=False)
            assert result == 0, "Expected successful run"

            state = synthesize._load_synth_state(state_path)
            assert state["current_run_id"] is None, "current_run_id should be None after completion"
            assert state["last_synth_session_mtime"] == mtime, "cursor mtime should be updated"
            assert state["last_synth_session_uuid"] == "session-001", "cursor uuid should be updated"
            assert state["sessions_since_last_synth"] == 0, "sessions counter should be reset"
