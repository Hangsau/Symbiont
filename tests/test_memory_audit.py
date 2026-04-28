"""
Tests for src/memory_audit.py — pure functions only
"""
import pytest
from datetime import date
from pathlib import Path

from src.memory_audit import (
    _parse_frontmatter,
    _parse_date,
    _archive_file,
    _archive_oldest_thoughts,
    THOUGHTS_ARCHIVE_BATCH_SIZE,
)


SAMPLE_FRONTMATTER = """\
---
name: test memory
description: test
type: user
valid_until: 2026-01-01
review_by: 2026-06-01
---

Body content here.
"""

SAMPLE_NO_FRONTMATTER = """\
# Just a title

Some content.
"""


# ── _parse_frontmatter ───────────────────────────────────────────

class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        result = _parse_frontmatter(SAMPLE_FRONTMATTER)
        assert result["valid_until"] == "2026-01-01"
        assert result["review_by"] == "2026-06-01"

    def test_no_frontmatter_returns_empty(self):
        result = _parse_frontmatter(SAMPLE_NO_FRONTMATTER)
        assert result == {}

    def test_null_value_returned_as_string(self):
        content = "---\nvalid_until: null\nreview_by: null\n---\n\nBody."
        result = _parse_frontmatter(content)
        assert result["valid_until"] == "null"
        assert result["review_by"] == "null"

    def test_only_target_fields_extracted(self):
        result = _parse_frontmatter(SAMPLE_FRONTMATTER)
        assert "name" not in result
        assert "description" not in result


# ── _parse_date ──────────────────────────────────────────────────

class TestParseDate:
    def test_valid_date(self):
        assert _parse_date("2026-04-28") == date(2026, 4, 28)

    def test_null_string(self):
        assert _parse_date("null") is None

    def test_none_string(self):
        assert _parse_date("none") is None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_invalid_format(self):
        assert _parse_date("not-a-date") is None

    def test_wrong_separator(self):
        assert _parse_date("28/04/2026") is None


# ── _archive_file (dry-run) ──────────────────────────────────────

class TestArchiveFileDryRun:
    def test_dry_run_does_not_move_file(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"
        index_path.write_text("- [test](test_file.md) — entry\n", encoding="utf-8")

        md_file = memory_dir / "test_file.md"
        md_file.write_text(SAMPLE_FRONTMATTER, encoding="utf-8")

        result = _archive_file(md_file, archive_dir, index_path, "2026-04-28", dry_run=True)

        assert result is True
        assert md_file.exists()                             # 原檔未移動
        assert not (archive_dir / "test_file.md").exists() # 目標未建立

    def test_dry_run_does_not_modify_memory_index(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"
        original_index = "- [test](test_file.md) — entry\n"
        index_path.write_text(original_index, encoding="utf-8")

        md_file = memory_dir / "test_file.md"
        md_file.write_text(SAMPLE_FRONTMATTER, encoding="utf-8")

        _archive_file(md_file, archive_dir, index_path, "2026-04-28", dry_run=True)

        assert index_path.read_text(encoding="utf-8") == original_index


# ── _archive_oldest_thoughts ─────────────────────────────────────

class TestArchiveOldestThoughts:
    def _make_thoughts(self, thoughts_dir: Path, names: list[str]):
        for name in names:
            (thoughts_dir / name).write_text(f"# {name}\n\nContent.", encoding="utf-8")

    def test_below_threshold_no_action(self, tmp_path):
        thoughts_dir = tmp_path / "thoughts"
        thoughts_dir.mkdir()
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        names = [f"2026-04-{i:02d}_note.md" for i in range(1, 6)]  # 5 files
        self._make_thoughts(thoughts_dir, names)

        count = _archive_oldest_thoughts(thoughts_dir, archive_dir, threshold=10, dry_run=False)

        assert count == 0
        assert len(list(thoughts_dir.glob("*.md"))) == 5

    def test_above_threshold_archives_oldest(self, tmp_path):
        thoughts_dir = tmp_path / "thoughts"
        thoughts_dir.mkdir()
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        names = [f"2026-04-{i:02d}_note.md" for i in range(1, 22)]  # 21 files
        self._make_thoughts(thoughts_dir, names)

        count = _archive_oldest_thoughts(thoughts_dir, archive_dir, threshold=20, dry_run=False)

        assert count == THOUGHTS_ARCHIVE_BATCH_SIZE
        # 最舊的應被刪除（按 stem 排序，01~10 最舊）
        for i in range(1, THOUGHTS_ARCHIVE_BATCH_SIZE + 1):
            assert not (thoughts_dir / f"2026-04-{i:02d}_note.md").exists()
        # 最新的應保留
        for i in range(THOUGHTS_ARCHIVE_BATCH_SIZE + 1, 22):
            assert (thoughts_dir / f"2026-04-{i:02d}_note.md").exists()

    def test_archives_by_stem_order_not_mtime(self, tmp_path):
        """歸檔順序依檔名（stem）而非 mtime，確保換機後不亂"""
        thoughts_dir = tmp_path / "thoughts"
        thoughts_dir.mkdir()
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        # 故意以反順序建立，讓 mtime 順序與 stem 順序相反
        names = [f"2026-04-{i:02d}_note.md" for i in range(20, 0, -1)]  # 20 files
        self._make_thoughts(thoughts_dir, names)

        _archive_oldest_thoughts(thoughts_dir, archive_dir, threshold=15, dry_run=False)

        # stem 排序最小（最早日期）= 01~10 應被刪除
        for i in range(1, THOUGHTS_ARCHIVE_BATCH_SIZE + 1):
            assert not (thoughts_dir / f"2026-04-{i:02d}_note.md").exists()

    def test_dry_run_no_file_changes(self, tmp_path):
        thoughts_dir = tmp_path / "thoughts"
        thoughts_dir.mkdir()
        archive_dir = tmp_path / "archive"
        archive_dir.mkdir()
        names = [f"2026-04-{i:02d}_note.md" for i in range(1, 22)]  # 21 files
        self._make_thoughts(thoughts_dir, names)

        _archive_oldest_thoughts(thoughts_dir, archive_dir, threshold=20, dry_run=True)

        # dry-run 不刪任何檔案
        assert len(list(thoughts_dir.glob("*.md"))) == 21
