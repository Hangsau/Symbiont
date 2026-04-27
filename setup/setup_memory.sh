#!/usr/bin/env bash
# setup_memory.sh — 初始化 Claude memory 系統骨架（Mac/Linux）
#
# 此腳本由 Claude 代為執行，或手動在 Symbiont 目錄下執行
# 會在 Claude Code 的主專案下建立 memory/ 目錄結構

set -euo pipefail

AGENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "============================================================"
echo " Symbiont memory 系統初始化"
echo "============================================================"
echo

# ── 找出 primary_project memory 路徑 ─────────────────────────────
MEMORY_DIR="$(cd "$AGENT_DIR" && python3 -c "
import sys
sys.path.insert(0, '.')
from src.utils.config_loader import load_config, get_path
cfg = load_config()
print(get_path(cfg, 'memory_dir'))
" 2>/dev/null)"

if [ -z "$MEMORY_DIR" ]; then
    echo "[錯誤] 無法解析 memory 路徑，請確認 Python 已安裝且 Symbiont 安裝完成"
    exit 1
fi

echo " memory 目錄：$MEMORY_DIR"
echo

# ── 建立目錄結構 ──────────────────────────────────────────────────
mkdir -p "$MEMORY_DIR"
mkdir -p "$MEMORY_DIR/archive"
mkdir -p "$MEMORY_DIR/thoughts"
echo "[1/3] 目錄建立完成"

# ── 建立 MEMORY.md（空索引）─────────────────────────────────────
if [ ! -f "$MEMORY_DIR/MEMORY.md" ]; then
    cat > "$MEMORY_DIR/MEMORY.md" << 'EOF'
# Memory Index

<!-- 每行格式：- [標題](檔名.md) — 一行描述 -->
<!-- Symbiont memory_audit.py 自動維護此索引 -->
EOF
    echo "[2/3] MEMORY.md 已建立"
else
    echo "[2/3] MEMORY.md 已存在，略過"
fi

# ── 複製 SCHEMA.md（從 Symbiont 模板）───────────────────────
SCHEMA_SRC="$AGENT_DIR/docs/MEMORY_SCHEMA.md"
SCHEMA_DEST="$MEMORY_DIR/SCHEMA.md"

if [ ! -f "$SCHEMA_DEST" ]; then
    if [ -f "$SCHEMA_SRC" ]; then
        cp "$SCHEMA_SRC" "$SCHEMA_DEST"
        echo "[3/3] SCHEMA.md 已複製"
    else
        echo "[3/3] SCHEMA.md 模板未找到（略過，可手動建立）"
    fi
else
    echo "[3/3] SCHEMA.md 已存在，略過"
fi

# ── 啟用 memory_audit ─────────────────────────────────────────────
echo
echo " 正在啟用 config.yaml memory_audit.enabled..."
python3 -c "
import re, pathlib
p = pathlib.Path('$AGENT_DIR/config.yaml')
content = p.read_text(encoding='utf-8')
content = re.sub(r'enabled:\s*false', 'enabled: true', content, count=1)
p.write_text(content, encoding='utf-8')
print('       enabled: true')
"

echo
echo "============================================================"
echo " memory 系統初始化完成！"
echo
echo " 下一步："
echo "   手動執行驗收 → python3 src/memory_audit.py --dry-run"
echo "   查看記憶目錄 → $MEMORY_DIR"
echo "============================================================"
