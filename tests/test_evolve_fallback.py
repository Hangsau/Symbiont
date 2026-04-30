"""
Tests for src/evolve.py — v2 state schema fallback logic.

Focus: _read_state, _write_state, _find_target_session with v2 schema.
Includes legacy v1 migration tests.
"""
import json
import time
from pathlib import Path

import pytest

from src.evolve import _read_state, _write_state, _default_state_v2, _migrate_state_v1_to_v2
from src.utils.session_reader import find_sessions_after


# ── Fixture: temp sessions directory with fake .jsonl ─────────────

@pytest.fixture
def tmp_sessions_dir(tmp_path):
    """Create a temp sessions directory structure."""
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    return sessions_dir


def _create_session(sessions_dir: Path, uuid: str, delay_sec: float = 0.01) -> Path:
    """Create a fake .jsonl session with given uuid, with sleep to ensure unique mtime."""
    if delay_sec > 0:
        time.sleep(delay_sec)
    path = sessions_dir / f"{uuid}.jsonl"
    # Minimal valid session content
    path.write_text('{"type": "user", "message": {"content": "test"}}\n', encoding="utf-8")
    return path


# ── Test 1: test_fallback_finds_oldest_unprocessed ──────────────

def test_fallback_finds_oldest_unprocessed(tmp_sessions_dir):
    """
    Scenario B: 5 sessions A, B, C, D, E (mtime ascending).
    processed_recent = [A, C, E] → fallback should find B (oldest unprocessed).

    Uses find_sessions_after to directly test the fallback logic.
    """
    # Create 5 sessions with strictly increasing mtimes
    path_a = _create_session(tmp_sessions_dir, "A", delay_sec=0.02)
    path_b = _create_session(tmp_sessions_dir, "B", delay_sec=0.02)
    path_c = _create_session(tmp_sessions_dir, "C", delay_sec=0.02)
    path_d = _create_session(tmp_sessions_dir, "D", delay_sec=0.02)
    path_e = _create_session(tmp_sessions_dir, "E", delay_sec=0.02)

    # Get mtime of A (this is our cursor)
    mtime_a = path_a.stat().st_mtime

    # Simulate state: last processed was A, and we've recently seen A, C, E
    # find_sessions_after should find B as the oldest unprocessed after cursor
    results = find_sessions_after(
        tmp_sessions_dir,
        after_mtime=mtime_a,
        after_uuid="A",
        excluded_uuids={"A", "C", "E"},
        limit=1,
    )

    assert len(results) == 1, "Should find exactly one session (B)"
    assert results[0].stem == "B", "Should find B (oldest unprocessed)"


# ── Test 2: test_fallback_no_unprocessed_returns_empty ──────────

def test_fallback_no_unprocessed_returns_empty(tmp_sessions_dir):
    """
    All 5 sessions in processed_recent → find_sessions_after returns empty list.
    """
    # Create 5 sessions
    path_a = _create_session(tmp_sessions_dir, "A", delay_sec=0.02)
    path_b = _create_session(tmp_sessions_dir, "B", delay_sec=0.02)
    path_c = _create_session(tmp_sessions_dir, "C", delay_sec=0.02)
    path_d = _create_session(tmp_sessions_dir, "D", delay_sec=0.02)
    path_e = _create_session(tmp_sessions_dir, "E", delay_sec=0.02)

    # Cursor is at the oldest (A)
    mtime_a = path_a.stat().st_mtime

    # All 5 are in processed_recent
    results = find_sessions_after(
        tmp_sessions_dir,
        after_mtime=mtime_a,
        after_uuid="A",
        excluded_uuids={"A", "B", "C", "D", "E"},
        limit=1,
    )

    assert results == [], "Should return empty list when all sessions processed"


# ── Test 3: test_legacy_v1_state_migration ───────────────────────

def test_legacy_v1_state_migration(tmp_path, tmp_sessions_dir):
    """
    Legacy v1 state.json has only last_processed_uuid and processed_at.
    _read_state should auto-migrate to v2 format with backup.
    """
    state_path = tmp_path / "state.json"

    # Create a v1 state (old schema)
    old_uuid = "legacy-session-uuid"
    old_state = {
        "last_processed_uuid": old_uuid,
        "processed_at": "2026-04-29T12:00:00+00:00"
    }
    state_path.write_text(json.dumps(old_state), encoding="utf-8")

    # Create a matching session file so mtime can be extracted
    _create_session(tmp_sessions_dir, old_uuid, delay_sec=0.01)
    session_path = tmp_sessions_dir / f"{old_uuid}.jsonl"
    expected_mtime = session_path.stat().st_mtime

    # Read state with migration
    migrated = _read_state(state_path, tmp_sessions_dir)

    # Verify v2 schema
    assert "last_processed_mtime" in migrated, "Should have last_processed_mtime"
    assert "processed_recent" in migrated, "Should have processed_recent"
    assert isinstance(migrated["processed_recent"], list), "processed_recent must be list"

    # Verify content
    assert migrated["last_processed_uuid"] == old_uuid
    assert migrated["last_processed_mtime"] == expected_mtime
    assert old_uuid in migrated["processed_recent"]
    assert migrated["processed_at"] == "2026-04-29T12:00:00+00:00"

    # Verify backup exists
    backup_path = state_path.with_name(state_path.name + ".pre_v2_backup")
    assert backup_path.exists(), "Backup file should be created"
    backup_content = json.loads(backup_path.read_text(encoding="utf-8"))
    assert backup_content["last_processed_uuid"] == old_uuid


# ── Test 4: test_processed_recent_circular_truncation ──────────────

def test_processed_recent_circular_truncation(tmp_path, tmp_sessions_dir):
    """
    _write_state keeps processed_recent at 50 items max (circular buffer).
    When adding a new UUID that already has 50 items,
    the oldest one should be dropped.
    """
    state_path = tmp_path / "state.json"

    # Create 50 UUIDs and populate processed_recent
    existing_uuids = [f"uuid-{i:03d}" for i in range(50)]
    initial_state = {
        "last_processed_mtime": 0.0,
        "last_processed_uuid": None,
        "processed_recent": existing_uuids,
        "processed_at": None,
    }
    state_path.write_text(json.dumps(initial_state), encoding="utf-8")

    # Create a session for the new UUID
    new_uuid = "uuid-new"
    jsonl_path = _create_session(tmp_sessions_dir, new_uuid, delay_sec=0.01)

    # Call _write_state with the new UUID
    _write_state(state_path, new_uuid, jsonl_path, dry_run=False)

    # Read back and verify
    state = json.loads(state_path.read_text(encoding="utf-8"))
    processed_recent = state["processed_recent"]

    # Should still be 50 items
    assert len(processed_recent) == 50, "processed_recent should stay at 50 items"

    # New UUID should be present
    assert new_uuid in processed_recent, "New UUID should be in processed_recent"

    # Oldest UUID (uuid-000) should be dropped
    assert "uuid-000" not in processed_recent, "Oldest UUID should be removed"

    # uuid-001 should still be present (it's now the oldest)
    assert "uuid-001" in processed_recent, "Second-oldest UUID should remain"

    # Last processed fields should be updated
    assert state["last_processed_uuid"] == new_uuid
    assert state["last_processed_mtime"] == jsonl_path.stat().st_mtime
