"""
backfill_tier.py — 對現有 memory/*.md 補上 tier: L1 | L2 欄位

功能：
  - 掃描 memory/*.md（排除 archive/、distilled/、_malformed/、thoughts/）
  - 跳過已有 tier: 欄位的檔案（冪等，中斷後可重跑）
  - 呼叫 claude -p 以 L1 三條件評估每個記憶
  - 將 tier 寫入 frontmatter
  - 失敗的檔案記入 error.log，繼續下一個

用法：
  python scripts/backfill_tier.py [--dry-run] [--limit N]

選項：
  --dry-run   只印出會做什麼，不寫入任何檔案
  --limit N   只處理前 N 個未分類的檔案（用於測試）
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config, get_path
from src.utils.claude_runner import run_claude
from src.utils.file_ops import safe_read, safe_write, append_log
from src.memory_audit import _set_frontmatter_field, NON_MEMORY_FILES

SKIP_DIRS = {"archive", "distilled", "_malformed", "thoughts"}

TIER_PROMPT = """\
評估以下記憶條目是否應注入每次 Claude Code session context（L1），還是按需查詢即可（L2）。

L1 條件（三條全中才選 L1）：
① 跨專案都會用到（不只針對特定 project）
② 沒預警就會需要它（session 開始前就必須知道）
③ 不知道就會立刻犯錯

記憶：
  名稱：{name}
  類型：{mem_type}
  說明：{description}

只回答 L1 或 L2，不要其他文字。"""

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FIELD_RE = re.compile(r"^tier\s*:", re.MULTILINE)


def _has_tier(content: str) -> bool:
    m = _FM_RE.match(content)
    if not m:
        return False
    return bool(_FIELD_RE.search(m.group(1)))


def _parse_field(content: str, field: str) -> str:
    m = _FM_RE.match(content)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip() == field:
            return v.strip().strip('"').strip("'")
    return ""


def _evaluate_tier(content: str, cfg: dict, default_tier: str) -> str:
    name = _parse_field(content, "name")
    mem_type = _parse_field(content, "type")
    description = _parse_field(content, "description")
    prompt = TIER_PROMPT.format(name=name, mem_type=mem_type, description=description)
    result = run_claude(prompt, cfg)
    if result is not None:
        tier = result.strip().upper()
        if tier in ("L1", "L2"):
            return tier
    return default_tier


def main() -> int:
    parser = argparse.ArgumentParser(description="backfill_tier.py — memory tier 補填")
    parser.add_argument("--dry-run", action="store_true", help="只印出不寫入")
    parser.add_argument("--limit", type=int, default=0, help="只處理前 N 個（0=全部）")
    args = parser.parse_args()

    cfg = load_config()
    tier_cfg = cfg.get("tier_classification", {})
    default_tier = tier_cfg.get("default_tier", "L2")
    error_log = get_path(cfg, "error_log")

    try:
        memory_dir = get_path(cfg, "memory_dir")
    except RuntimeError as e:
        print(f"[backfill_tier] 路徑解析失敗：{e}", file=sys.stderr)
        return 1

    if not memory_dir.exists():
        print(f"[backfill_tier] memory/ 不存在：{memory_dir}", file=sys.stderr)
        return 1

    # 收集待處理檔案
    candidates: list[Path] = []
    for md_path in sorted(memory_dir.glob("*.md")):
        if md_path.name in NON_MEMORY_FILES:
            continue
        content = safe_read(md_path)
        if not content:
            continue
        if _has_tier(content):
            continue  # 已分類，跳過
        candidates.append(md_path)

    # 掃子目錄（排除 skip_dirs）
    for subdir in memory_dir.iterdir():
        if not subdir.is_dir() or subdir.name in SKIP_DIRS:
            continue
        for md_path in sorted(subdir.glob("*.md")):
            content = safe_read(md_path)
            if not content:
                continue
            if _has_tier(content):
                continue
            candidates.append(md_path)

    total = len(candidates)
    if args.limit > 0:
        candidates = candidates[:args.limit]

    print(f"[backfill_tier] 待分類：{total} 個，本次處理：{len(candidates)} 個"
          f"{'（dry-run）' if args.dry_run else ''}")

    ok_count = 0
    fail_count = 0

    for md_path in candidates:
        content = safe_read(md_path)
        if not content:
            continue

        name = _parse_field(content, "name") or md_path.name

        if args.dry_run:
            print(f"  [dry-run] 會評估：{md_path.name}（name: {name}）")
            ok_count += 1
            continue

        try:
            tier = _evaluate_tier(content, cfg, default_tier)
            updated = _set_frontmatter_field(content, "tier", tier)
            if safe_write(md_path, updated):
                print(f"  ✓ {md_path.name} → tier: {tier}")
                ok_count += 1
            else:
                raise RuntimeError("safe_write 回傳 False")
        except Exception as e:
            ts = datetime.utcnow().isoformat(timespec="seconds")
            msg = f"[{ts}] backfill_tier 失敗 {md_path.name}: {e}"
            append_log(error_log, msg)
            print(f"  ✗ {md_path.name} → 失敗（記入 error.log）", file=sys.stderr)
            fail_count += 1

    print(f"[backfill_tier] 完成：成功 {ok_count}，失敗 {fail_count}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
