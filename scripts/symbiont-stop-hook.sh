#!/usr/bin/env bash
# symbiont-stop-hook.sh — Claude Code Stop hook
#
# 觸發時機：Claude Code session 結束時
# 輸入：stdin 接收 JSON（Claude Code hook payload）
# 行為：
#   1. 解析 session_id
#   2. 寫 pending_evolve.txt（含 session_id）
#   3. 寫 pending_audit.txt（旗標）
#   4. 背景啟動 evolve.py（延遲 30 秒）
#
# 驗證方式：
#   echo '{"session_id":"test-uuid-123"}' | bash ~/.claude/scripts/symbiont-stop-hook.sh

set -euo pipefail

# ── 路徑設定 ──────────────────────────────────────────────────────
# LOCAL_AGENT_DIR 優先序：環境變數 > 自動偵測（同 scripts/ 的上層 projects/local-agent）
if [ -n "${LOCAL_AGENT_DIR:-}" ]; then
    AGENT_DIR="$LOCAL_AGENT_DIR"
else
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    # ~/.claude/scripts/ → 往上找 local-agent（支援常見安裝位置）
    CANDIDATES=(
        "$HOME/claudehome/projects/local-agent"
        "$HOME/projects/local-agent"
        "$HOME/local-agent"
    )
    AGENT_DIR=""
    for c in "${CANDIDATES[@]}"; do
        if [ -f "$c/config.yaml" ]; then
            AGENT_DIR="$c"
            break
        fi
    done
fi

if [ -z "$AGENT_DIR" ] || [ ! -f "$AGENT_DIR/config.yaml" ]; then
    echo "[symbiont-stop-hook] Symbiont 目錄未找到，跳過" >&2
    echo "[symbiont-stop-hook] 設定 LOCAL_AGENT_DIR 環境變數指向安裝路徑" >&2
    exit 0
fi

DATA_DIR="$AGENT_DIR/data"
mkdir -p "$DATA_DIR"

# ── 解析 session_id ───────────────────────────────────────────────
PAYLOAD="$(cat)"
SESSION_ID=""

# 嘗試用 python 解析 JSON（更可靠）
if command -v python3 &>/dev/null; then
    SESSION_ID="$(echo "$PAYLOAD" | python3 -c "
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get('session_id', '') or d.get('sessionId', ''))
except Exception:
    pass
" 2>/dev/null || true)"
fi

# fallback：grep 直接取值
if [ -z "$SESSION_ID" ]; then
    SESSION_ID="$(echo "$PAYLOAD" | grep -oP '(?<="session_id"\s*:\s*")[^"]+' 2>/dev/null || true)"
fi

# ── 寫 pending 旗標檔 ─────────────────────────────────────────────
echo "$SESSION_ID" > "$DATA_DIR/pending_evolve.txt"
echo "triggered" > "$DATA_DIR/pending_audit.txt"

# ── 背景啟動 evolve.py（延遲 30 秒）─────────────────────────────
# 確保背景 subshell 能找到 claude CLI（npm/nvm/Homebrew 路徑在 hook 環境常缺失）
# Windows: npm global bin
# Mac: Homebrew + nvm（取最新版）
# Linux: ~/.local/bin + nvm
NVM_LATEST="$(ls -d "$HOME/.nvm/versions/node/"*/bin 2>/dev/null | sort -V | tail -1 || true)"
export PATH="$HOME/AppData/Roaming/npm:$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:${NVM_LATEST}:$PATH"

# Windows 上 bash subshell & 在 hook 結束時會被砍掉，不用背景啟動。
# 改由 Task Scheduler 每分鐘定時檢查 pending_evolve.txt 是否存在再執行 evolve.py。
# Mac/Linux：仍用 bash subshell 背景執行。
if [[ "$OSTYPE" == "msys"* ]] || [[ "$OSTYPE" == "cygwin"* ]] || [[ -n "${WINDIR:-}" ]]; then
    echo "[symbiont-stop-hook] pending files written, evolve will run via Task Scheduler"
else
    PYTHON_CMD="python3"
    if ! command -v python3 &>/dev/null; then
        PYTHON_CMD="python"
    fi
    NVM_LATEST="$(ls -d "$HOME/.nvm/versions/node/"*/bin 2>/dev/null | sort -V | tail -1 || true)"
    export PATH="$HOME/.local/bin:/usr/local/bin:/opt/homebrew/bin:${NVM_LATEST}:$PATH"
    (
        sleep 30
        cd "$AGENT_DIR"
        $PYTHON_CMD src/evolve.py --skip-if-wrap-done \
            >> "$DATA_DIR/evolve_hook.log" 2>&1
    ) &
    echo "[symbiont-stop-hook] pending files written, evolve.py scheduled (30s)"
fi
exit 0
