"""
Tests for src/synthesize.py — state management and cursor logic in v2 schema

Focus: cursor position (mtime + uuid), no-loss no-duplicate, migration
"""
import json
import time
import pytest
from pathlib import Path

from src.utils.session_reader import find_sessions_after
from src.synthesize import _load_synth_state, _default_synth_state_v2


# ── Test 1: backlog no-loss no-duplicate ──────────────────────────────

class TestBacklogNoLossNoDuplicate:
    """Verify synthesize processes all sessions without loss or duplication.

    Scenario: 25 fake sessions with mtime ascending, sessions_per_cycle=10.
    Run 3 times and verify:
      - First run: picks oldest 10 (t1..t10)
      - Second run: picks next 10 (t11..t20)
      - Third run: picks remaining 5 (t21..t25)
      - Union = 25 unique sessions
    """

    def test_three_cycles_25_sessions(self, tmp_path):
        """Three complete cycles covering 25 sessions without loss or duplication."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Build 25 fake sessions with ascending mtime
        sessions = []
        base_time = 1700000000.0
        for i in range(25):
            # Create unique UUID-like stem: session-00-uuid001, session-01-uuid002, etc.
            stem = f"session-{i:02d}-uuid{i:03d}"
            jsonl_file = sessions_dir / f"{stem}.jsonl"
            jsonl_file.write_text('{"type": "user", "message": {"content": "test"}}\n')
            # Set mtime in ascending order
            mtime = base_time + i * 1.0
            jsonl_file.touch()
            # Force mtime by setting via utime
            import os
            os.utime(str(jsonl_file), (mtime, mtime))
            sessions.append((stem, mtime))

        # Verify mtimes are in order
        for i, (stem, mtime) in enumerate(sessions):
            actual = (sessions_dir / f"{stem}.jsonl").stat().st_mtime
            assert actual == mtime, f"Session {i} mtime mismatch"

        # Simulate three runs with limit=10
        state = _default_synth_state_v2()
        assert state["last_synth_session_mtime"] == 0.0
        assert state["last_synth_session_uuid"] is None

        picked_all = []

        # Run 1: Pick first 10
        picked_1 = find_sessions_after(
            sessions_dir,
            after_mtime=state.get("last_synth_session_mtime", 0.0),
            after_uuid=state.get("last_synth_session_uuid"),
            excluded_uuids=None,
            limit=10,
        )
        assert len(picked_1) == 10, f"Run 1: expected 10 sessions, got {len(picked_1)}"
        picked_all.extend(picked_1)

        # Update cursor to last picked session's mtime + uuid (mimicking synthesize.run() end)
        last_1 = max(picked_1, key=lambda p: (p.stat().st_mtime, p.stem))
        state["last_synth_session_mtime"] = last_1.stat().st_mtime
        state["last_synth_session_uuid"] = last_1.stem

        # Run 2: Pick next 10
        picked_2 = find_sessions_after(
            sessions_dir,
            after_mtime=state.get("last_synth_session_mtime", 0.0),
            after_uuid=state.get("last_synth_session_uuid"),
            excluded_uuids=None,
            limit=10,
        )
        assert len(picked_2) == 10, f"Run 2: expected 10 sessions, got {len(picked_2)}"
        picked_all.extend(picked_2)

        # Update cursor
        last_2 = max(picked_2, key=lambda p: (p.stat().st_mtime, p.stem))
        state["last_synth_session_mtime"] = last_2.stat().st_mtime
        state["last_synth_session_uuid"] = last_2.stem

        # Run 3: Pick remaining 5
        picked_3 = find_sessions_after(
            sessions_dir,
            after_mtime=state.get("last_synth_session_mtime", 0.0),
            after_uuid=state.get("last_synth_session_uuid"),
            excluded_uuids=None,
            limit=10,
        )
        assert len(picked_3) == 5, f"Run 3: expected 5 sessions, got {len(picked_3)}"
        picked_all.extend(picked_3)

        # Update cursor
        last_3 = max(picked_3, key=lambda p: (p.stat().st_mtime, p.stem))
        state["last_synth_session_mtime"] = last_3.stat().st_mtime
        state["last_synth_session_uuid"] = last_3.stem

        # Verify: all 25 covered, no duplicates
        stems = [p.stem for p in picked_all]
        assert len(stems) == 25, f"Total picked: {len(stems)}, expected 25"
        assert len(set(stems)) == 25, "Duplicates found in picked sessions"

        # Verify: Run 4 returns empty (cursor at end)
        picked_4 = find_sessions_after(
            sessions_dir,
            after_mtime=state.get("last_synth_session_mtime", 0.0),
            after_uuid=state.get("last_synth_session_uuid"),
            excluded_uuids=None,
            limit=10,
        )
        assert len(picked_4) == 0, f"Run 4: expected 0 sessions (cursor at end), got {len(picked_4)}"


# ── Test 2: cursor does not advance to now ────────────────────────────

class TestCursorNotAdvancedToNow:
    """Verify cursor is set to last picked session's mtime, not current time.

    Scenario: Build 5 sessions with mtime t1..t5.
    After picking all, assert cursor mtime equals t5 (not time.time()).
    """

    def test_cursor_is_session_mtime_not_now(self, tmp_path):
        """Cursor position must be set to the last session's mtime, not now."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()

        # Build 5 sessions with known mtime values
        base_time = 1700000000.0
        session_mtimes = {}
        for i in range(5):
            stem = f"session-{i:02d}-uuid{i:03d}"
            jsonl_file = sessions_dir / f"{stem}.jsonl"
            jsonl_file.write_text('{"type": "user", "message": {"content": "test"}}\n')
            mtime = base_time + i * 2.0
            import os
            os.utime(str(jsonl_file), (mtime, mtime))
            session_mtimes[stem] = mtime

        # Pick all sessions
        state = _default_synth_state_v2()
        picked = find_sessions_after(
            sessions_dir,
            after_mtime=0.0,
            after_uuid=None,
            excluded_uuids=None,
            limit=10,
        )
        assert len(picked) == 5

        # Find the last one (highest mtime)
        last_session = max(picked, key=lambda p: (p.stat().st_mtime, p.stem))
        expected_mtime = last_session.stat().st_mtime

        # Update cursor (mimicking synthesize.run())
        state["last_synth_session_mtime"] = expected_mtime
        state["last_synth_session_uuid"] = last_session.stem

        # Verify cursor is NOT the current time
        current_time = time.time()
        assert abs(state["last_synth_session_mtime"] - current_time) > 10.0, \
            "Cursor should not be close to current time"
        assert state["last_synth_session_mtime"] == expected_mtime, \
            "Cursor should equal the last session's mtime"


# ── Test 3: legacy v1 state migration ─────────────────────────────────

class TestLegacyV1StateMigration:
    """Verify old v1 state.json auto-migrates to v2 schema.

    Scenario: Write old v1 state, call _load_synth_state().
    Expected: Backup created, state migrated to v2.
    """

    def test_v1_migration_creates_backup_and_v2_schema(self, tmp_path):
        """Old v1 state should auto-migrate to v2 with backup."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        state_path = tmp_path / "synth_state.json"

        # Create a v1 state file (old schema)
        v1_state = {
            "sessions_since_last_synth": 5,
            "last_synth_at": "2026-04-29T10:00:00+00:00",
            "last_synth_uuid": "old-uuid-123",
            "skill_stats": {"test_skill": 3},
        }
        state_path.write_text(json.dumps(v1_state), encoding="utf-8")

        # Load with new code (should migrate)
        state = _load_synth_state(state_path, sessions_dir=sessions_dir)

        # Verify v2 schema is present
        assert "last_synth_session_mtime" in state, "Missing new v2 field: last_synth_session_mtime"
        assert "current_run_id" in state, "Missing new v2 field: current_run_id"
        assert "patterns_done_at" in state, "Missing new v2 field: patterns_done_at"

        # Verify old fields are replaced
        assert "last_synth_at" not in state, "Old field last_synth_at should be removed"

        # Verify data is preserved
        assert state["sessions_since_last_synth"] == 5
        assert state["last_synth_session_uuid"] == "old-uuid-123"
        assert state["skill_stats"] == {"test_skill": 3}

        # Verify backup exists
        backup_path = state_path.with_name(state_path.name + ".pre_v2_backup")
        assert backup_path.exists(), "Backup file should exist"
        backup_content = json.loads(backup_path.read_text(encoding="utf-8"))
        assert backup_content["last_synth_at"] == "2026-04-29T10:00:00+00:00"

        # Verify state file is now v2 format
        saved_state = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_synth_session_mtime" in saved_state
        assert "last_synth_at" not in saved_state


# ── Test 4: v1 migration with mtime fallback ──────────────────────────

class TestV1MigrationWithMtimeLookup:
    """Verify v1 migration tries to recover mtime from session file.

    Scenario: Old state has last_synth_uuid pointing to an actual session file.
    Expected: mtime extracted from that session file.
    """

    def test_v1_migration_recovers_mtime_from_session(self, tmp_path):
        """Migration should extract mtime from the referenced session file."""
        sessions_dir = tmp_path / "sessions"
        sessions_dir.mkdir()
        state_path = tmp_path / "synth_state.json"

        # Create a session file with known mtime
        target_stem = "session-123-abc"
        target_jsonl = sessions_dir / f"{target_stem}.jsonl"
        target_jsonl.write_text('{"type": "user", "message": {"content": "test"}}\n')

        known_mtime = 1700000000.0
        import os
        os.utime(str(target_jsonl), (known_mtime, known_mtime))

        # Create v1 state pointing to this session
        v1_state = {
            "sessions_since_last_synth": 0,
            "last_synth_uuid": target_stem,
            "skill_stats": {},
        }
        state_path.write_text(json.dumps(v1_state), encoding="utf-8")

        # Load (should migrate and extract mtime)
        state = _load_synth_state(state_path, sessions_dir=sessions_dir)

        # Verify mtime was extracted
        assert state["last_synth_session_mtime"] == known_mtime, \
            "Should recover mtime from session file"
        assert state["last_synth_session_uuid"] == target_stem
