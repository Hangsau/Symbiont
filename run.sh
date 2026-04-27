#!/usr/bin/env bash
# local-agent Mac/Linux 入口腳本
# 用途：launchd / cron 呼叫 / 手動執行
#
# 用法：
#   ./run.sh evolve [--dry-run]
#   ./run.sh memory_audit [--dry-run]
#   ./run.sh babysit
#
# launchd plist 範例（每天 02:00 memory_audit）：
#   ProgramArguments: ["/Users/xxx/claudehome/projects/local-agent/run.sh", "memory_audit"]
#   StartCalendarInterval: {Hour: 2, Minute: 0}

set -euo pipefail

# ── 切換到腳本所在目錄（確保相對路徑正確）────────────────────
cd "$(dirname "$0")"

# ── 確保 claude CLI 在 PATH ────────────────────────────────────
# Homebrew / npm global 常見路徑
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

# ── 參數檢查 ───────────────────────────────────────────────────
if [ $# -eq 0 ]; then
    echo "Usage: $0 <script> [options]"
    echo "  Scripts: evolve, memory_audit, babysit"
    echo "  Options: --dry-run"
    exit 1
fi

SCRIPT="$1"
shift

# ── 執行 ───────────────────────────────────────────────────────
python3 "src/${SCRIPT}.py" "$@"
