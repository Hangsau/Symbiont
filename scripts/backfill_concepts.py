"""
backfill_concepts.py — 對現有 memory/*.md 補上 concepts: [...] 欄位

功能：
  - 掃描 memory/*.md（排除 archive/、distilled/、_malformed/、thoughts/）
  - 跳過已有 concepts: 欄位的檔案（冪等，中斷後可重跑）
  - 呼叫 claude -p 根據 name/description/body 產生 2–5 個 kebab-case 語意標籤
  - 將 concepts 寫入 frontmatter
  - LLM 失敗的檔案記入 error.log，繼續下一個（不寫空 concepts，保留重跑機會）

用法：
  python scripts/backfill_concepts.py [--dry-run] [--limit N]

選項：
  --dry-run   只印出會做什麼，不寫入任何檔案
  --limit N   只處理前 N 個未分類的檔案（用於測試）
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config, get_path
from src.utils.claude_runner import run_claude
from src.utils.file_ops import safe_read, safe_write, append_log
from src.memory_audit import _set_frontmatter_field, NON_MEMORY_FILES

_BODY_EXCERPT_MAX_CHARS = 400

CONCEPTS_PROMPT = """\
根據以下記憶條目，產生 2–5 個 kebab-case 語意標籤，用於語意搜尋。

規則：
- 每個標籤為 kebab-case（全小寫，用 - 連接）
- 涵蓋核心概念、相關概念、上位概念
- 只輸出 JSON 陣列，不含任何解釋文字

記憶資訊：
  名稱：{name}
  類型：{mem_type}
  說明：{description}
  內容摘要：{body_excerpt}

輸出格式（只輸出 JSON）：
["concept-1", "concept-2", "concept-3"]"""

_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_FIELD_RE = re.compile(r"^concepts\s*:", re.MULTILINE)


def _has_concepts(content: str) -> bool:
    m = _FM_RE.match(content)
    if not m:
        return True  # 無 frontmatter，無法寫入，跳過
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


def _extract_body(content: str) -> str:
    """frontmatter 之後的正文，取前 _BODY_EXCERPT_MAX_CHARS 字元。"""
    m = _FM_RE.match(content)
    if not m:
        return content[:_BODY_EXCERPT_MAX_CHARS]
    body = content[m.end():]
    return body[:_BODY_EXCERPT_MAX_CHARS].strip()


def _format_concepts(tags: list) -> str:
    """將 list 序列化為 YAML inline 格式字串，如 [tag1, tag2]。"""
    inner = ", ".join(str(t) for t in tags)
    return f"[{inner}]"


def _evaluate_concepts(content: str, cfg: dict) -> list | None:
    """呼叫 LLM 產生 concepts；失敗或解析失敗回傳 None（不寫入）。"""
    name = _parse_field(content, "name")
    mem_type = _parse_field(content, "type")
    description = _parse_field(content, "description")
    body_excerpt = _extract_body(content)

    prompt = CONCEPTS_PROMPT.format(
        name=name,
        mem_type=mem_type,
        description=description,
        body_excerpt=body_excerpt,
    )
    result = run_claude(prompt, cfg)
    if not result:
        return None

    try:
        match = re.search(r"\[.*?\]", result, re.DOTALL)
        if not match:
            return None
        tags = json.loads(match.group())
        if not isinstance(tags, list):
            return None
        cleaned = [t.strip().lower() for t in tags if isinstance(t, str) and t.strip()]
        return cleaned if cleaned else None
    except (json.JSONDecodeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="backfill_concepts.py — memory concepts 補填")
    parser.add_argument("--dry-run", action="store_true", help="只印出不寫入")
    parser.add_argument("--limit", type=int, default=0, help="只處理前 N 個（0=全部）")
    args = parser.parse_args()

    cfg = load_config()
    error_log = get_path(cfg, "error_log")

    try:
        memory_dir = get_path(cfg, "memory_dir")
    except RuntimeError as e:
        print(f"[backfill_concepts] 路徑解析失敗：{e}", file=sys.stderr)
        return 1

    if not memory_dir.exists():
        print(f"[backfill_concepts] memory/ 不存在：{memory_dir}", file=sys.stderr)
        return 1

    # 收集待處理檔案（root-level only）
    candidates: list[Path] = []
    for md_path in sorted(memory_dir.glob("*.md")):
        if md_path.name in NON_MEMORY_FILES:
            continue
        content = safe_read(md_path)
        if not content:
            continue
        if _has_concepts(content):
            continue
        candidates.append(md_path)

    total = len(candidates)
    if args.limit > 0:
        candidates = candidates[:args.limit]

    print(f"[backfill_concepts] 待補填：{total} 個，本次處理：{len(candidates)} 個"
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
            tags = _evaluate_concepts(content, cfg)
            if tags is None:
                raise RuntimeError("LLM 未回傳有效 JSON 陣列")
            formatted = _format_concepts(tags)
            updated = _set_frontmatter_field(content, "concepts", formatted)
            if safe_write(md_path, updated):
                print(f"  [ok] {md_path.name} -> concepts: {formatted}")
                ok_count += 1
            else:
                raise RuntimeError("safe_write 回傳 False")
        except Exception as e:
            ts = datetime.utcnow().isoformat(timespec="seconds")
            msg = f"[{ts}] backfill_concepts 失敗 {md_path.name}: {e}"
            append_log(error_log, msg)
            print(f"  [fail] {md_path.name} -> 失敗（記入 error.log）", file=sys.stderr)
            fail_count += 1

    print(f"[backfill_concepts] 完成：成功 {ok_count}，失敗 {fail_count}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
