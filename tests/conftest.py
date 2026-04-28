"""
pytest 共用 fixtures
"""
import sys
from pathlib import Path

# 確保 local-agent/ 根目錄在 sys.path，讓 from src.xxx import 正常運作
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


# ── 樣本 CLAUDE.md 內容 ──────────────────────────────────────────

SAMPLE_CLAUDE_MD_WITH_SECTION = """\
# Test CLAUDE.md

## 工作方式

- 動手前輸出執行計畫

---

## 自動學習規則

- 舊規則1
- 舊規則2

## 其他 section

- 其他內容
"""

SAMPLE_CLAUDE_MD_NO_SECTION = """\
# Test CLAUDE.md

## 工作方式

- 動手前輸出執行計畫
"""


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def claude_md_with_section():
    return SAMPLE_CLAUDE_MD_WITH_SECTION


@pytest.fixture
def claude_md_no_section():
    return SAMPLE_CLAUDE_MD_NO_SECTION


@pytest.fixture
def tmp_claude_md(tmp_path):
    """建立含 ## 自動學習規則 section 的暫存 CLAUDE.md"""
    p = tmp_path / "CLAUDE.md"
    p.write_text(SAMPLE_CLAUDE_MD_WITH_SECTION, encoding="utf-8")
    return p


@pytest.fixture
def mock_cfg(tmp_path):
    """最小化 config dict，路徑全指向 tmp_path，不觸碰真實系統。"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return {
        "_root": str(tmp_path),
        "paths": {
            "claude_projects_base": str(tmp_path / "projects"),
            "primary_project": "",
            "global_claude_md": str(tmp_path / "CLAUDE.md"),
            "wrap_done_file": str(tmp_path / ".wrap_done.txt"),
            "evolution_log": str(data_dir / "evolution_log.md"),
            "state_file": str(data_dir / "state.json"),
            "pending_evolve": str(data_dir / "pending_evolve.txt"),
            "error_log": str(data_dir / "error.log"),
            "audit_log": str(data_dir / "audit.log"),
            "pending_audit": str(data_dir / "pending_audit.txt"),
        },
        "claude_runner": {"timeout_seconds": 10, "max_retries": 1},
        "session_reader": {"max_turns": 50},
        "evolve": {"distill_threshold": 25},
        "memory_audit": {
            "enabled": True,
            "auto_archive": True,
            "thoughts_archive_threshold": 30,
            "memory_index_warn_lines": 170,
        },
        "babysit": {
            "teaching_timeout_seconds": 1800,
            "lock_max_age_seconds": 900,
        },
    }
