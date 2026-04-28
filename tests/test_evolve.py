"""
Tests for src/evolve.py — pure functions only, no LLM calls
"""
import pytest
from src.evolve import (
    _extract_json,
    _validate_output,
    _validate_distill_output,
    _append_rules_to_claude_md,
    _replace_section_rules,
    _find_section_bounds,
    EVOLVE_SECTION_TITLE,
    EVOLVE_SECTION_HEADER,
)


# ── _extract_json ────────────────────────────────────────────────

class TestExtractJson:
    def test_pure_json(self):
        raw = '{"rules_to_add": [], "summary": "test"}'
        result = _extract_json(raw)
        assert result == {"rules_to_add": [], "summary": "test"}

    def test_markdown_fence(self):
        raw = '```json\n{"rules_to_add": [], "summary": "test"}\n```'
        result = _extract_json(raw)
        assert result is not None
        assert result["summary"] == "test"

    def test_brace_counting_with_preamble(self):
        """brace-counting 處理有前綴文字但無 fence 的輸出"""
        raw = 'Here is the output:\n{"rules_to_add": [], "summary": "ok"}'
        result = _extract_json(raw)
        assert result is not None
        assert result["summary"] == "ok"

    def test_fence_handles_braces_in_string(self):
        """Fence fallback 能正確處理字串內含 {} 的情況"""
        raw = '```json\n{"summary": "use {} format", "rules_to_add": []}\n```'
        result = _extract_json(raw)
        assert result is not None
        assert result["summary"] == "use {} format"

    def test_direct_parse_handles_braces_in_string(self):
        """無前綴時 direct parse 可 handle 字串內含 }"""
        raw = '{"summary": "use } format", "rules_to_add": []}'
        result = _extract_json(raw)
        assert result is not None
        assert result["summary"] == "use } format"

    def test_invalid_returns_none(self):
        assert _extract_json("this is not json at all") is None

    def test_empty_returns_none(self):
        assert _extract_json("") is None

    def test_invalid_fence_content_returns_none(self):
        assert _extract_json("```json\nnot json\n```") is None


# ── _validate_output ─────────────────────────────────────────────

class TestValidateOutput:
    def test_valid(self):
        data = {"rules_to_add": [{"content": "- rule"}], "summary": "summary"}
        assert _validate_output(data) is True

    def test_empty_rules_valid(self):
        data = {"rules_to_add": [], "summary": "nothing new"}
        assert _validate_output(data) is True

    def test_missing_summary(self):
        data = {"rules_to_add": []}
        assert _validate_output(data) is False

    def test_rules_not_list(self):
        data = {"rules_to_add": "not a list", "summary": "ok"}
        assert _validate_output(data) is False

    def test_rule_missing_content(self):
        data = {"rules_to_add": [{"text": "wrong key"}], "summary": "ok"}
        assert _validate_output(data) is False

    def test_not_dict(self):
        assert _validate_output([]) is False
        assert _validate_output("string") is False


# ── _validate_distill_output ─────────────────────────────────────

class TestValidateDistillOutput:
    def _make_rules(self, n):
        return [{"content": f"- rule {i}"} for i in range(n)]

    def test_valid_reduction(self):
        # existing=12, new=3 → total=15; distilled=10 < 15 ✓; distilled=10 >= 5 ✓
        data = {
            "distilled_rules": self._make_rules(10),
            "merge_summary": "merged",
            "removed_count": 5,
        }
        assert _validate_distill_output(data, 12, 3) is True

    def test_four_rules_rejected(self):
        """4 條 < 最低門檻 5，應被拒絕（設計決策：防止 LLM 過度裁剪）"""
        data = {
            "distilled_rules": self._make_rules(4),
            "merge_summary": "merged",
            "removed_count": 8,
        }
        assert _validate_distill_output(data, 10, 2) is False

    def test_five_rules_accepted(self):
        """恰好 5 條，且有縮減，應通過"""
        data = {
            "distilled_rules": self._make_rules(5),
            "merge_summary": "merged",
            "removed_count": 7,
        }
        assert _validate_distill_output(data, 10, 2) is True

    def test_no_reduction_rejected(self):
        """輸出數 >= 輸入總數，應被拒絕"""
        # existing=8, new=4 → total=12; distilled=12 >= 12 → rejected
        data = {
            "distilled_rules": self._make_rules(12),
            "merge_summary": "no change",
            "removed_count": 0,
        }
        assert _validate_distill_output(data, 8, 4) is False

    def test_missing_dash_prefix_rejected(self):
        """規則未以 '- ' 開頭，應被拒絕"""
        rules = [{"content": "rule without dash"}] + self._make_rules(7)
        data = {
            "distilled_rules": rules,
            "merge_summary": "merged",
            "removed_count": 2,
        }
        assert _validate_distill_output(data, 8, 2) is False

    def test_missing_merge_summary_rejected(self):
        data = {"distilled_rules": self._make_rules(8), "removed_count": 2}
        assert _validate_distill_output(data, 8, 2) is False

    def test_removed_count_not_int_rejected(self):
        data = {
            "distilled_rules": self._make_rules(8),
            "merge_summary": "ok",
            "removed_count": "two",
        }
        assert _validate_distill_output(data, 10, 2) is False


# ── _find_section_bounds ─────────────────────────────────────────

class TestFindSectionBounds:
    def test_found(self, claude_md_with_section):
        bounds = _find_section_bounds(claude_md_with_section)
        assert bounds is not None
        start, end = bounds
        section = claude_md_with_section[start:end]
        assert section.startswith(EVOLVE_SECTION_TITLE)
        assert "舊規則1" in section
        assert "其他 section" not in section

    def test_not_found(self, claude_md_no_section):
        assert _find_section_bounds(claude_md_no_section) is None

    def test_no_next_section_end_is_eof(self):
        """沒有後續 section 時 end 應指向檔尾"""
        content = "# Doc\n\n## 自動學習規則\n\n- rule1\n- rule2\n"
        bounds = _find_section_bounds(content)
        assert bounds is not None
        _, end = bounds
        assert end == len(content)

    def test_section_content_excludes_next_header(self, claude_md_with_section):
        """section range 不包含下一個 ## header"""
        bounds = _find_section_bounds(claude_md_with_section)
        start, end = bounds
        section = claude_md_with_section[start:end]
        assert "## 其他 section" not in section
        assert "其他內容" not in section


# ── _append_rules_to_claude_md ───────────────────────────────────

class TestAppendRules:
    def test_append_to_existing_section(self, claude_md_with_section):
        rules = [{"content": "- 新規則"}]
        result = _append_rules_to_claude_md(claude_md_with_section, rules)
        assert "- 新規則" in result
        assert "舊規則1" in result
        assert "舊規則2" in result

    def test_new_rule_before_other_section(self, claude_md_with_section):
        """新規則應插入在下一個 section 之前"""
        rules = [{"content": "- 新規則"}]
        result = _append_rules_to_claude_md(claude_md_with_section, rules)
        assert result.index("新規則") < result.index("其他 section")

    def test_other_section_content_preserved(self, claude_md_with_section):
        rules = [{"content": "- 新規則A"}, {"content": "- 新規則B"}]
        result = _append_rules_to_claude_md(claude_md_with_section, rules)
        assert "## 其他 section" in result
        assert "其他內容" in result

    def test_creates_section_if_missing(self, claude_md_no_section):
        rules = [{"content": "- 第一條規則"}]
        result = _append_rules_to_claude_md(claude_md_no_section, rules)
        assert EVOLVE_SECTION_TITLE in result
        assert "第一條規則" in result

    def test_empty_rules_returns_unchanged(self, claude_md_with_section):
        result = _append_rules_to_claude_md(claude_md_with_section, [])
        assert result == claude_md_with_section

    def test_multiple_rules_all_appended(self, claude_md_with_section):
        rules = [{"content": f"- 規則{i}"} for i in range(3)]
        result = _append_rules_to_claude_md(claude_md_with_section, rules)
        for i in range(3):
            assert f"- 規則{i}" in result


# ── _replace_section_rules ───────────────────────────────────────

class TestReplaceSectionRules:
    def _make_rule_strs(self, n):
        return [f"- 蒸餾規則{i}" for i in range(n)]

    def test_old_rules_removed(self, claude_md_with_section):
        result = _replace_section_rules(claude_md_with_section, self._make_rule_strs(6))
        assert "舊規則1" not in result
        assert "舊規則2" not in result

    def test_new_rules_present(self, claude_md_with_section):
        new_rules = self._make_rule_strs(6)
        result = _replace_section_rules(claude_md_with_section, new_rules)
        for r in new_rules:
            assert r in result

    def test_other_sections_preserved(self, claude_md_with_section):
        result = _replace_section_rules(claude_md_with_section, self._make_rule_strs(6))
        assert "## 其他 section" in result
        assert "其他內容" in result

    def test_header_intact(self, claude_md_with_section):
        result = _replace_section_rules(claude_md_with_section, self._make_rule_strs(6))
        assert EVOLVE_SECTION_HEADER in result

    def test_preceding_content_preserved(self, claude_md_with_section):
        result = _replace_section_rules(claude_md_with_section, self._make_rule_strs(6))
        assert "## 工作方式" in result
        assert "動手前輸出執行計畫" in result
