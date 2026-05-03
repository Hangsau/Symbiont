"""
run_search.py — search_memory.py 的 CLI/hook 入口

用法：
  python scripts/run_search.py "如何讓 agent 提取記憶"

規則：
  - 任何 exception 一律 exit 0（不卡 hook / 不中斷 session）
  - 只是轉呼叫 search_memory.main()，不含額外邏輯
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from src.search_memory import main
    main()
except Exception as e:
    print(f"[run_search] error: {e}", file=sys.stderr)
    sys.exit(0)
