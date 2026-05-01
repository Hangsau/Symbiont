#!/usr/bin/env bash
# uninstall_mac.sh — 移除 Symbiont（macOS）
# 執行完畢後，請手動刪除 Symbiont 資料夾本身

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AGENT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "============================================================"
echo " Symbiont 移除程式"
echo "============================================================"
echo

# ── 1. 移除 launchd plist ────────────────────────────────────
echo "[1/3] 移除 launchd 排程..."

PLIST_DIR="$HOME/Library/LaunchAgents"
PLISTS=(
    "com.symbiont.evolve.plist"
    "com.symbiont.memory-audit.plist"
    "com.symbiont.babysit.plist"
    "com.symbiont.session-wrap.plist"
)

for plist in "${PLISTS[@]}"; do
    plist_path="$PLIST_DIR/$plist"
    if [ -f "$plist_path" ]; then
        launchctl unload "$plist_path" 2>/dev/null || true
        rm "$plist_path"
        echo "      已移除：$plist"
    else
        echo "      略過（不存在）：$plist"
    fi
done

# ── 2. 移除 Stop hook（~/.claude/settings.json）─────────────
echo
echo "[2/3] 移除 Stop hook from ~/.claude/settings.json..."

python3 - <<'PYEOF'
import json, sys, pathlib, os

p = pathlib.Path(os.path.expanduser("~/.claude/settings.json"))
if not p.exists():
    print("      settings.json 不存在，略過")
    sys.exit(0)

cfg = json.loads(p.read_text(encoding="utf-8"))
hooks = cfg.get("hooks", {})
stop_hooks = hooks.get("Stop", [])
before = len(stop_hooks)
stop_hooks = [h for h in stop_hooks if "evolve" not in str(h) and "symbiont" not in str(h)]
removed = before - len(stop_hooks)
if removed:
    hooks["Stop"] = stop_hooks
    cfg["hooks"] = hooks
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"      已移除 {removed} 個 hook")
else:
    print("      無 Symbiont hook，略過")
PYEOF

# ── 3. 刪除 wrap_done_file 及 session_wrap 狀態檔 ───────────
echo
echo "[3/3] 清除暫態旗標檔..."

WRAP_DONE="$HOME/.claude/.wrap_done.txt"
if [ -f "$WRAP_DONE" ]; then
    rm "$WRAP_DONE"
    echo "      已刪除：$WRAP_DONE"
else
    echo "      不存在（略過）：$WRAP_DONE"
fi

SESSION_WRAP_STATE="$AGENT_DIR/data/session_wrap_state.json"
if [ -f "$SESSION_WRAP_STATE" ]; then
    rm "$SESSION_WRAP_STATE"
    echo "      已刪除：$SESSION_WRAP_STATE"
else
    echo "      不存在（略過）：$SESSION_WRAP_STATE"
fi

SESSION_WRAP_PENDING="$AGENT_DIR/data/pending_session_wrap.txt"
if [ -f "$SESSION_WRAP_PENDING" ]; then
    rm "$SESSION_WRAP_PENDING"
    echo "      已刪除：$SESSION_WRAP_PENDING"
else
    echo "      不存在（略過）：$SESSION_WRAP_PENDING"
fi

# ── 完成 ─────────────────────────────────────────────────────
echo
echo "============================================================"
echo " 完成！請手動刪除 Symbiont 資料夾："
echo " $AGENT_DIR"
echo "============================================================"
echo
