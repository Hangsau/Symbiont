"""
habit_extractor.py — 從 session turns 提取習慣片段（Track B）

習慣片段 = 用戶重複發起同類型任務請求的對話段落，是 Workflow / Audit skill 的原料。

提供：
  extract_habit_fragments(turns, max_chars) → str
"""

from src.utils.turn_utils import extract_context

# 任務啟動句型（user turn 開頭或包含 → 可能是新任務請求）
HABIT_SIGNALS = [
    "幫我", "來做", "規劃", "寫完", "完成了", "幫忙",
    "可以幫", "請幫", "開始", "建立", "新增", "實作",
]

# 排除明顯是「對 Claude 系統本身下指令」的句型（避免污染）
EXCLUDE_SIGNALS = [
    "幫我建 skill", "幫我建立 skill", "來做 M", "來做規劃",
    "幫我規劃 M", "幫我寫 synthesize", "幫我寫 evolve",
]


def _find_habit_turns(turns: list[dict]) -> list[int]:
    """回傳 user turns 中出現習慣信號的索引列表（排除系統開發相關指令）。"""
    indices = []
    for i, turn in enumerate(turns):
        if turn["role"] != "user":
            continue
        content = turn.get("content", "")
        if any(exc in content for exc in EXCLUDE_SIGNALS):
            continue
        if any(sig in content for sig in HABIT_SIGNALS):
            indices.append(i)
    return indices


def extract_habit_fragments(turns: list[dict], max_chars: int = 800) -> str:
    """提取習慣片段，總長度限制在 max_chars 以內。

    每個命中的 user turn + 緊接的 assistant 回應前 200 chars。
    去重後串接。
    """
    indices = _find_habit_turns(turns)
    if not indices:
        return ""

    seen: set[int] = set()
    fragments: list[str] = []
    total = 0

    for idx in indices:
        # 取 user turn + 下一個 assistant turn（若存在）
        context = extract_context(turns, idx, window=1)
        for turn in context:
            tid = id(turn)
            if tid in seen:
                continue
            seen.add(tid)
            # user turn 完整取；assistant turn 只取前 200 chars
            limit = 400 if turn["role"] == "user" else 200
            snippet = turn["content"][:limit]
            role = turn["role"].upper()
            line = f"[{role}] {snippet}"
            if total + len(line) > max_chars:
                return "\n".join(fragments)
            fragments.append(line)
            total += len(line)

    return "\n".join(fragments)
