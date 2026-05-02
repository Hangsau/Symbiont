#!/usr/bin/env bash
# Hermes Agent VM bootstrap：用 claude -p 自動安裝並啟動 Hermes agent
#
# 使用方式（在 VM 上執行）：bash run.sh
#
# 本機觸發方式：
#   macOS / Linux：
#     ssh user@your-vm "bash ~/run.sh"
#   Windows PowerShell / Git Bash：
#     ssh user@your-vm "bash ~/run.sh"
#   （run.sh 本身在 VM 上跑，本機只需能 SSH 即可）
#
# 前置條件：
#   1. ~/.claude/.credentials.json 已存在（從本機 SCP）
#   2. ~/secrets.env 已填好（從 secrets.example.env 複製並填入真實值）
#
# 工作流程：
#   1. 驗證 credentials.json 存在
#   2. 讀取 SETUP.md 的完整指令
#   3. 呼叫 claude -p 自動執行安裝流程

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_FILE="$SCRIPT_DIR/SETUP.md"

if [ ! -f "$SETUP_FILE" ]; then
    echo "Error: SETUP.md not found at $SETUP_FILE"
    exit 1
fi

if [ ! -f "$HOME/.claude/.credentials.json" ]; then
    echo "Warning: ~/.claude/.credentials.json not found"
    echo ""
    echo "Claude Code 需要認證才能執行。請先在本機執行："
    echo ""
    echo "  scp -i ~/.ssh/id_ed25519 ~/.claude/.credentials.json user@your-vm:~/.claude/.credentials.json"
    echo ""
    echo "或在 VM 上首次執行 'claude -p \"hello\"' 來完成互動式登入。"
    exit 1
fi

if ! command -v claude &> /dev/null; then
    echo "Error: claude CLI 不在 PATH 中"
    echo "請確認 Claude Code 已安裝（npm install -g @anthropic-ai/claude-code）"
    echo "並確認 npm global bin 在 PATH：export PATH=\"\$(npm bin -g):\$PATH\""
    exit 1
fi

echo "=== Hermes Agent VM Bootstrap: 啟動設置 ==="
echo "SETUP.md: $SETUP_FILE"
echo ""

# 執行安裝：讀取 SETUP.md，傳給 claude -p，限制可用工具為 Bash/Read/Write/Edit
claude -p "$(cat "$SETUP_FILE")" --allowedTools "Bash,Read,Write,Edit"

echo ""
echo "=== Bootstrap 完成 ==="
