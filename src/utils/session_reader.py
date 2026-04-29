"""
session_reader.py — 解析 Claude Code .jsonl session log

sessions_dir = ~/.claude/projects/（所有專案的根目錄）
遞迴掃所有子目錄，不限於特定專案。

輸出：[{"role": "user"|"assistant", "content": str, "timestamp": str}, ...]

用法（CLI 驗收）：
    python src/utils/session_reader.py            # 印最近 session 前 5 條
    python src/utils/session_reader.py <uuid>     # 指定 session uuid
    python src/utils/session_reader.py --list     # 列出所有 session（mtime 排序）
"""

import json
import sys
from pathlib import Path

# 允許從任意工作目錄呼叫
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.config_loader import load_config, get_path, get_int


# ── 解析單一 .jsonl ────────────────────────────────────────────

def _extract_text_from_content(content) -> str:
    """從 assistant content（list of blocks）取出純文字。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", "").strip())
            # thinking / tool_use / tool_result blocks → 略過
        return "\n".join(parts).strip()
    return ""


def parse_session(jsonl_path: Path, max_turns: int = 50) -> list[dict]:
    """解析 .jsonl，回傳最後 max_turns 條對話（user + assistant）。"""
    turns = []

    try:
        lines = jsonl_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        print(f"[session_reader] 無法讀取 {jsonl_path}: {e}", file=sys.stderr)
        return []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_type = obj.get("type")

        if msg_type == "user":
            msg = obj.get("message", {})
            content = msg.get("content", "")
            text = _extract_text_from_content(content)
            if text:
                turns.append({
                    "role": "user",
                    "content": text,
                    "timestamp": obj.get("timestamp", ""),
                })

        elif msg_type == "assistant":
            msg = obj.get("message", {})
            content = msg.get("content", [])
            text = _extract_text_from_content(content)
            if text:
                turns.append({
                    "role": "assistant",
                    "content": text,
                    "timestamp": obj.get("timestamp", ""),
                })

        # permission-mode / file-history-snapshot / summary → 略過

    # 只保留最後 max_turns 條
    return turns[-max_turns:] if len(turns) > max_turns else turns


# ── 尋找最新 session ───────────────────────────────────────────

def list_sessions(sessions_dir: Path) -> list[Path]:
    """遞迴回傳 sessions_dir 下所有專案的 .jsonl，依 mtime 降序排列（最新在前）。"""
    files = list(sessions_dir.rglob("*.jsonl"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def find_latest_session(sessions_dir: Path) -> Path | None:
    """回傳所有專案中最新的 .jsonl，若無任何 session 則回傳 None。"""
    files = list_sessions(sessions_dir)
    return files[0] if files else None


def find_session_by_uuid(sessions_dir: Path, uuid: str) -> Path | None:
    """用 uuid（含或不含 .jsonl）在所有專案子目錄中尋找 session 檔案。"""
    name = uuid if uuid.endswith(".jsonl") else f"{uuid}.jsonl"
    matches = list(sessions_dir.rglob(name))
    return matches[0] if matches else None


def find_sessions_since(sessions_dir: Path, after_ts: float, limit: int) -> list[Path]:
    """回傳 mtime > after_ts 的 session 檔案，依 mtime 升序，取最舊到最新的 limit 個。

    Args:
        sessions_dir: ~/.claude/projects/（遞迴掃所有子目錄）
        after_ts:     Unix timestamp（上次 synthesis 時間），0.0 = 取最新 limit 個
        limit:        最多回傳幾個 session

    Returns:
        list[Path]，mtime 升序（最舊在前，最新在後）
    """
    files = [
        p for p in sessions_dir.rglob("*.jsonl")
        if p.stat().st_mtime > after_ts
    ]
    files.sort(key=lambda p: p.stat().st_mtime)
    return files[-limit:] if len(files) > limit else files


# ── CLI 入口 ───────────────────────────────────────────────────

def main():
    cfg = load_config()
    sessions_dir = get_path(cfg, "sessions_dir")
    max_turns = get_int(cfg, "session_reader", "max_turns", default=50)

    if not sessions_dir.exists():
        print(f"[session_reader] sessions_dir 不存在：{sessions_dir}")
        sys.exit(1)

    args = sys.argv[1:]

    # --list：列出所有 session
    if args and args[0] == "--list":
        files = list_sessions(sessions_dir)
        if not files:
            print("（無 session 檔案）")
        for f in files:
            print(f.name)
        return

    # 指定 uuid
    if args and not args[0].startswith("--"):
        path = find_session_by_uuid(sessions_dir, args[0])
        if not path:
            print(f"[session_reader] 找不到 session：{args[0]}")
            sys.exit(1)
    else:
        path = find_latest_session(sessions_dir)
        if not path:
            print("[session_reader] sessions_dir 內無 .jsonl 檔案")
            sys.exit(1)

    print(f"Session: {path.name}")
    print(f"Sessions dir: {sessions_dir}")
    print("=" * 60)

    turns = parse_session(path, max_turns=max_turns)

    # 驗收：印前 5 條
    preview = turns[:5]
    for i, turn in enumerate(preview, 1):
        role = turn["role"].upper()
        content = turn["content"]
        # 截斷顯示
        display = content[:200] + "…" if len(content) > 200 else content
        print(f"\n[{i}] {role} ({turn['timestamp'][:19]})")
        print(display)

    print(f"\n（共 {len(turns)} 條對話，顯示前 {len(preview)} 條）")


if __name__ == "__main__":
    # Windows cp950 終端機無法顯示 UTF-8 字元，強制換成 UTF-8
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
