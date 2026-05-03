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
    _prune_oldest_index_entries,
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


# ── _prune_oldest_index_entries ──────────────────────────────────

class TestPruneOldestIndexEntries:
    TODAY = "2026-05-03"

    def _make_memory_file(self, memory_dir: Path, name: str) -> Path:
        f = memory_dir / name
        f.write_text(f"# {name}\n\nContent.\n", encoding="utf-8")
        return f

    def _make_index(self, index_path: Path, entries: list[str], thoughts: list[str] | None = None):
        lines = ["# Memory Index\n"]
        for e in entries:
            lines.append(f"- [{e}]({e}) — desc\n")
        if thoughts is not None:
            lines.append("\n## Thoughts\n")
            for t in thoughts:
                lines.append(f"- [{t}](thoughts/{t}) — desc\n")
        index_path.write_text("".join(lines), encoding="utf-8")

    def test_archives_oldest_non_thoughts_entries(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"

        entries = [f"entry_{i:02d}.md" for i in range(1, 11)]  # 10 entries
        for name in entries:
            self._make_memory_file(memory_dir, name)
        thoughts = ["2026-05-01_thought.md"]
        (memory_dir / "thoughts").mkdir()
        (memory_dir / "thoughts" / "2026-05-01_thought.md").write_text("# t\n", encoding="utf-8")
        self._make_index(index_path, entries, thoughts)

        count = _prune_oldest_index_entries(
            memory_dir, index_path, archive_dir, batch_size=3,
            today_str=self.TODAY, dry_run=False
        )

        assert count == 3
        # 最舊 3 條（entry_01, 02, 03）已移至 archive/
        for i in range(1, 4):
            assert not (memory_dir / f"entry_{i:02d}.md").exists()
            assert (archive_dir / f"entry_{i:02d}.md").exists()
        # 其餘保留
        for i in range(4, 11):
            assert (memory_dir / f"entry_{i:02d}.md").exists()
        # Thoughts 條目未被歸檔
        assert (memory_dir / "thoughts" / "2026-05-01_thought.md").exists()
        # 已歸檔條目的索引行已移除
        index_text = index_path.read_text(encoding="utf-8")
        for i in range(1, 4):
            assert f"entry_{i:02d}.md" not in index_text
        for i in range(4, 11):
            assert f"entry_{i:02d}.md" in index_text

    def test_dry_run_no_changes(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"

        entries = [f"entry_{i:02d}.md" for i in range(1, 6)]
        for name in entries:
            self._make_memory_file(memory_dir, name)
        self._make_index(index_path, entries)
        original_index = index_path.read_text(encoding="utf-8")

        count = _prune_oldest_index_entries(
            memory_dir, index_path, archive_dir, batch_size=3,
            today_str=self.TODAY, dry_run=True
        )

        assert count == 3
        assert index_path.read_text(encoding="utf-8") == original_index  # 未改動
        for name in entries:
            assert (memory_dir / name).exists()  # 所有檔案保留

    def test_empty_index_returns_zero(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"
        index_path.write_text("", encoding="utf-8")

        count = _prune_oldest_index_entries(
            memory_dir, index_path, archive_dir, batch_size=5,
            today_str=self.TODAY, dry_run=False
        )

        assert count == 0

    def test_no_candidates_returns_zero(self, tmp_path):
        """MEMORY.md 只有 Thoughts 條目時，不歸檔任何東西"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"
        self._make_index(index_path, [], thoughts=["2026-05-01_t.md"])

        count = _prune_oldest_index_entries(
            memory_dir, index_path, archive_dir, batch_size=5,
            today_str=self.TODAY, dry_run=False
        )

        assert count == 0

    def test_orphan_index_line_only_removes_line(self, tmp_path):
        """索引行對應的 .md 檔不存在（孤兒行）→ 只刪索引行，不 crash"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"
        # orphan.md 不存在於 memory_dir
        index_path.write_text(
            "- [orphan](orphan.md) — gone\n"
            "- [alive](alive.md) — here\n",
            encoding="utf-8",
        )
        (memory_dir / "alive.md").write_text("# alive\n", encoding="utf-8")

        count = _prune_oldest_index_entries(
            memory_dir, index_path, archive_dir, batch_size=1,
            today_str=self.TODAY, dry_run=False
        )

        assert count == 1
        index_text = index_path.read_text(encoding="utf-8")
        assert "orphan.md" not in index_text   # 孤兒行已移除
        assert "alive.md" in index_text        # 第二條保留

    def test_thoughts_section_skipped(self, tmp_path):
        """## Thoughts 之後的條目不計入候選，即使路徑不含 thoughts/"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"

        # 在 Thoughts section 之後放兩個「看起來是普通條目」的行
        index_path.write_text(
            "- [real_entry](real_entry.md) — normal\n"
            "\n## Thoughts\n"
            "- [thought_a](thought_a.md) — in thoughts section\n"
            "- [thought_b](thought_b.md) — in thoughts section\n",
            encoding="utf-8",
        )
        self._make_memory_file(memory_dir, "real_entry.md")
        self._make_memory_file(memory_dir, "thought_a.md")
        self._make_memory_file(memory_dir, "thought_b.md")

        count = _prune_oldest_index_entries(
            memory_dir, index_path, archive_dir, batch_size=5,
            today_str=self.TODAY, dry_run=False
        )

        # 只有 real_entry 被歸檔，Thoughts section 的條目不動
        assert count == 1
        assert not (memory_dir / "real_entry.md").exists()
        assert (memory_dir / "thought_a.md").exists()
        assert (memory_dir / "thought_b.md").exists()

    def test_respects_batch_size(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        archive_dir = memory_dir / "archive"
        archive_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"

        entries = [f"entry_{i:02d}.md" for i in range(1, 21)]  # 20 entries
        for name in entries:
            self._make_memory_file(memory_dir, name)
        self._make_index(index_path, entries)

        count = _prune_oldest_index_entries(
            memory_dir, index_path, archive_dir, batch_size=7,
            today_str=self.TODAY, dry_run=False
        )

        assert count == 7
        remaining = [f for f in memory_dir.glob("*.md") if f.name != "MEMORY.md"]
        assert len(remaining) == 13  # 20 - 7
