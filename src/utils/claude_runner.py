"""
claude_runner.py — claude -p subprocess 封裝

功能：
  - auth 檢查（確認 ~/.claude/.credentials.json 存在）
  - subprocess 呼叫 claude -p
  - retry 最多 max_retries 次（相同 prompt，格式處理由呼叫方負責）
  - timeout 保護
  - 失敗 → 寫 error.log，回傳 None（不拋例外）

用法：
    from utils.claude_runner import run_claude
    result = run_claude(prompt_text, cfg)
    if result is None:
        # 呼叫失敗，已記入 error.log
"""

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.config_loader import load_config, get_path, get_int, get_str
from src.utils.file_ops import append_log


def check_auth() -> bool:
    """確認 claude CLI 已登入。"""
    return (Path.home() / ".claude" / ".credentials.json").exists()


def _find_claude_unix() -> str | None:
    """Mac/Linux：在常見安裝路徑搜尋 claude 執行檔。"""
    home = Path.home()
    candidates = [
        "/usr/local/bin/claude",
        str(home / ".local/bin/claude"),
        "/opt/homebrew/bin/claude",       # Mac Homebrew (Apple Silicon)
        "/usr/bin/claude",
    ]
    # nvm 路徑：~/.nvm/versions/node/*/bin/claude（取最新版）
    nvm_bins = sorted((home / ".nvm/versions/node").glob("*/bin/claude"), reverse=True)
    candidates += [str(p) for p in nvm_bins]

    return next((p for p in candidates if Path(p).exists()), None)


def _resolve_cmd(cli: str) -> list[str]:
    """
    回傳實際可執行的命令列表，確保跨平台、跨觸發環境（hook/cron/background）均可找到 claude。

    問題根因：背景進程繼承的 PATH 比互動 shell 少，npm/nvm/Homebrew 路徑常缺失。
    策略：
      - Windows：.cmd 不是原生執行檔 → 找 node.exe + cli.js 直呼叫
      - Mac/Linux：若 cli 找不到 → 掃常見安裝路徑（含 nvm）
    """
    cli_path = Path(cli)

    if sys.platform == "win32":
        # 若 config 給了絕對路徑且存在，直接用
        if cli_path.is_absolute() and cli_path.exists():
            return [str(cli_path)]

        # 自動偵測：npm global node_modules（使用 ~ 展開，不寫死用戶名）
        npm_bin = Path.home() / "AppData" / "Roaming" / "npm"

        # 優先：native EXE（claude-code 較新版本）
        exe = npm_bin / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return [str(exe)]

        # Fallback：node.exe + cli.js（舊版或其他安裝方式）
        cli_js = npm_bin / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        node_candidates = [
            Path(r"C:\Program Files\nodejs\node.exe"),
            Path(r"C:\Program Files (x86)\nodejs\node.exe"),
            npm_bin / "node.exe",
        ]
        node_exe = next((p for p in node_candidates if p.exists()), None)
        if node_exe and cli_js.exists():
            return [str(node_exe), str(cli_js)]

        return [cli]

    # Mac / Linux：cli 若直接可執行就用，否則掃常見路徑
    if cli_path.is_absolute() and cli_path.exists():
        return [cli]

    # "claude" 等非絕對路徑：先試 PATH，失敗就掃已知位置
    import shutil
    found = shutil.which(cli)
    if found:
        return [found]

    fallback = _find_claude_unix()
    if fallback:
        return [fallback]

    return [cli]  # 最後保留原值，讓 FileNotFoundError 自然浮現


def _call_claude(cli: str, prompt: str, timeout: int) -> tuple[bool, str]:
    """呼叫一次 claude -p。回傳 (success, output_or_error_msg)。"""
    try:
        cmd = _resolve_cmd(cli) + ["-p", prompt, "--output-format", "text"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, f"returncode={result.returncode} stderr={result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    except FileNotFoundError:
        return False, f"claude CLI not found: {cli}"
    except Exception as e:
        return False, f"unexpected error: {e}"


def run_claude(prompt: str, cfg: dict | None = None) -> str | None:
    """
    呼叫 claude -p，含 retry 與 error logging。
    成功回傳輸出字串，失敗回傳 None。
    """
    if cfg is None:
        cfg = load_config()

    cli = get_str(cfg, "paths", "claude_cli", default="claude")
    timeout = get_int(cfg, "claude_runner", "timeout_seconds", default=120)
    max_retries = get_int(cfg, "claude_runner", "max_retries", default=2)
    error_log = get_path(cfg, "error_log")

    if not check_auth():
        msg = "auth check failed: ~/.claude/.credentials.json not found"
        append_log(error_log, f"[claude_runner] {msg}")
        print(f"[claude_runner] {msg}", file=sys.stderr)
        return None

    for attempt in range(1, max_retries + 1):
        success, output = _call_claude(cli, prompt, timeout)
        if success:
            return output
        if attempt < max_retries:
            time.sleep(2)

    ts = datetime.now().isoformat(timespec="seconds")
    msg = f"[{ts}] failed after {max_retries} attempts: {output}"
    append_log(error_log, f"[claude_runner] {msg}")
    print(f"[claude_runner] {msg}", file=sys.stderr)
    return None
