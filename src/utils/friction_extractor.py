"""
friction_extractor.py — 從 session turns 提取摩擦片段（Track A）

摩擦片段 = 用戶糾正或 assistant 退讓的對話段落，是 Guard skill 的原料。

提供：
  extract_friction_fragments(turns, max_chars) → str
"""

from src.utils.turn_utils import extract_context

# 糾正信號詞（user turn 出現 → 可能是在糾正 assistant）
FRICTION_SIGNALS = [
    "不對", "不是", "你又", "改成", "重來", "不行",
    "錯了", "不要這樣", "我說的不是", "不是這樣",
]

# assistant 退讓信號詞
BACKTRACK_SIGNALS = [
    "我理解錯了", "重新", "抱歉", "我誤解", "更正",
]


def _find_friction_turns(turns: list[dict]) -> list[int]:
    """回傳 turns 中出現摩擦信號的索引列表。"""
    indices = []
    for i, turn in enumerate(turns):
        content = turn.get("content", "")
        if turn["role"] == "user":
            if any(sig in content for sig in FRICTION_SIGNALS):
                indices.append(i)
        elif turn["role"] == "assistant":
            if any(sig in content for sig in BACKTRACK_SIGNALS):
                indices.append(i)
    return indices


def extract_friction_fragments(turns: list[dict], max_chars: int = 1500) -> str:
    """提取摩擦片段，總長度限制在 max_chars 以內。

    每個摩擦點取前後各 1 輪，截斷至 300 chars/turn，去重後串接。
    """
    indices = _find_friction_turns(turns)
    if not indices:
        return ""

    seen: set[int] = set()
    fragments: list[str] = []
    total = 0

    for idx in indices:
        context = extract_context(turns, idx, window=1)
        for turn in context:
            # 用 id() 去重（同一 turn 物件不重複輸出）
            tid = id(turn)
            if tid in seen:
                continue
            seen.add(tid)
            snippet = turn["content"][:300]
            role = turn["role"].upper()
            line = f"[{role}] {snippet}"
            if total + len(line) > max_chars:
                return "\n".join(fragments)
            fragments.append(line)
            total += len(line)

    return "\n".join(fragments)
