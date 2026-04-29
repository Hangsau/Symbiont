"""
turn_utils.py — session turn 操作共用工具

提供：
  extract_context(turns, idx, window) → list[dict]
    回傳 turns[idx] 及前後 window 輪的對話，供 extractor 使用。
"""


def extract_context(turns: list[dict], idx: int, window: int = 1) -> list[dict]:
    """回傳以 idx 為中心、前後各 window 輪的 turns 子集。

    Args:
        turns:  parse_session 回傳的 turn list，每個 turn 是 {"role", "content", ...}
        idx:    中心 turn 的索引
        window: 前後各取幾輪（預設 1）

    Returns:
        turns[max(0, idx-window) : idx+window+1]
    """
    start = max(0, idx - window)
    end = idx + window + 1
    return turns[start:end]
