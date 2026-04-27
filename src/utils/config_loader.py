"""
config_loader.py — 載入 config.yaml 並解析所有路徑

使用範例：
    from utils.config_loader import load_config, get_path
    cfg = load_config()
    sessions_base = get_path(cfg, 'sessions_dir')   # 全域掃描根目錄
    memory_dir    = get_path(cfg, 'memory_dir')     # 主專案 memory 目錄

設計原則：
  sessions_dir（全域）：~/.claude/projects/
    evolve.py 遞迴掃此目錄下所有 .jsonl，不限於特定專案

  primary_project_dir（主專案）：~/.claude/projects/{encoded}/
    memory_audit.py 操作此子目錄的 memory/
    來源優先序：env var LOCAL_AGENT_PRIMARY_PROJECT > config primary_project > 自動偵測

  wrap_done_file：~/.claude/.wrap_done.txt（固定路徑，config.yaml 可覆蓋）
"""

import os
import re
from pathlib import Path

import yaml


def _find_config() -> Path:
    """從腳本所在位置往上找 config.yaml（支援從任何子目錄呼叫）。"""
    candidates = [
        Path(__file__).parent,
        Path(__file__).parent.parent,
        Path(__file__).parent.parent.parent,
    ]
    for d in candidates:
        p = d / "config.yaml"
        if p.exists():
            return p
    raise FileNotFoundError("config.yaml not found. Expected at local-agent root.")


def load_config(config_path: str | None = None) -> dict:
    """載入並回傳 config dict。預設自動尋找 config.yaml。"""
    path = Path(config_path) if config_path else _find_config()
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = str(path.parent.resolve())
    return cfg


def _expand(raw: str, root: str) -> Path:
    """展開 ~ 和相對路徑（相對於 config 根目錄）。"""
    p = Path(raw).expanduser()
    if p.is_absolute():
        return p.resolve()
    return (Path(root) / p).resolve()


def _encode_primary_project(workdir: Path) -> str:
    """把工作目錄路徑編碼成 Claude Code 的 projects/ 子目錄名。

    規則：路徑中的 :、\\、/ 全部替換為 -
      Windows  C:\\claudehome     → C--claudehome
      Mac/Linux /Users/xxx/home  → -Users-xxx-home
    """
    return re.sub(r"[:/\\]", "-", str(workdir.resolve()))


def _autodetect_primary_project_dir(base: Path) -> Path:
    """掃 ~/.claude/projects/，取最近有 .jsonl session 的子目錄。"""
    if not base.exists():
        raise RuntimeError(f"Claude projects dir not found: {base}")

    best_dir: Path | None = None
    best_mtime: float = 0.0

    for d in base.iterdir():
        if not d.is_dir():
            continue
        for jsonl in d.glob("*.jsonl"):
            try:
                mtime = jsonl.stat().st_mtime
                if mtime > best_mtime:
                    best_mtime = mtime
                    best_dir = d
            except OSError:
                continue

    if best_dir is None:
        raise RuntimeError(
            "Cannot auto-detect primary_project_dir: no .jsonl sessions found in "
            f"{base}. "
            "Set LOCAL_AGENT_PRIMARY_PROJECT env var or config.yaml primary_project."
        )
    return best_dir


def get_path(cfg: dict, key: str) -> Path:
    """取得指定 key 的解析後 Path。

    自動推算的 key（不需在 config.yaml 填寫）：
        sessions_dir        — ~/.claude/projects/（全域 session 掃描根目錄）
        primary_project_dir — 主專案子目錄（memory 操作用）
        memory_dir          — primary_project_dir/memory
        memory_index        — memory_dir/MEMORY.md
        global_claude_md    — ~/.claude/CLAUDE.md
        wrap_done_file      — ~/.claude/.wrap_done.txt（config.yaml 可覆蓋）
        evolution_log       — data/evolution_log.md（config.yaml 可覆蓋）

    config.yaml 明確設定可覆蓋任何自動推算值（留空 "" = 使用自動推算）。
    """
    paths = cfg["paths"]
    root = cfg["_root"]
    base = _expand(paths["claude_projects_base"], root)

    if key == "sessions_dir":
        # 全域：回傳 ~/.claude/projects/ 本身，session_reader 遞迴掃所有子目錄
        return base

    if key == "primary_project_dir":
        # 主專案（memory 操作用）：env var > config > 自動偵測
        env = os.environ.get("LOCAL_AGENT_PRIMARY_PROJECT", "").strip()
        if env:
            return base / _encode_primary_project(Path(env))
        raw = paths.get("primary_project", "").strip()
        if raw:
            return base / _encode_primary_project(_expand(raw, root))
        return _autodetect_primary_project_dir(base)

    if key == "memory_dir":
        return get_path(cfg, "primary_project_dir") / "memory"

    if key == "memory_index":
        return get_path(cfg, "memory_dir") / "MEMORY.md"

    if key == "global_claude_md":
        raw = paths.get("global_claude_md", "").strip()
        if raw:
            return _expand(raw, root)
        return Path.home() / ".claude" / "CLAUDE.md"

    if key == "wrap_done_file":
        raw = paths.get("wrap_done_file", "").strip()
        if raw:
            return _expand(raw, root)
        return Path.home() / ".claude" / ".wrap_done.txt"

    if key == "evolution_log":
        raw = paths.get("evolution_log", "").strip()
        if raw:
            return _expand(raw, root)
        return Path(root) / "data" / "evolution_log.md"

    raw = paths.get(key, "")
    if not raw:
        return Path()
    return _expand(str(raw), root)


def get_str(cfg: dict, *keys: str, default: str = "") -> str:
    """取得巢狀設定值（字串）。e.g. get_str(cfg, 'claude_runner', 'timeout_seconds')"""
    node = cfg
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return str(node)


def get_int(cfg: dict, *keys: str, default: int = 0) -> int:
    """取得巢狀設定值（整數）。"""
    return int(get_str(cfg, *keys, default=str(default)))
