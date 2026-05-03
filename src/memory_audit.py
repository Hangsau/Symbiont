"""
memory_audit.py — 自動維護 Claude Code memory 系統

功能：
  1. valid_until 非 null 的條目 → 自動歸檔至 archive/，從 MEMORY.md 移除
  2. review_by 到期的條目 → 列入 audit.log，不自動處理（留人工判斷）
  3. thoughts/ 超過閾值 → 最舊 N 條追加摘要至 archive/thoughts-index.md 後刪除
  4. MEMORY.md 超過行數閾值 → 輸出警告

觸發方式：
  - Stop hook → 寫 pending_audit.txt → 本腳本（開機補跑）
  - 手動：python src/memory_audit.py [--dry-run]

設定：
  config.yaml memory_audit.enabled = true 才會執行
  config.yaml memory_audit.auto_archive = false 時只報告不移檔
"""

import argparse
from contextlib import nullcontext
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config, get_path, get_int
from src.utils.file_ops import safe_read, safe_write, append_log, FileLock


# ── 常數 ──────────────────────────────────────────────────────────

NON_MEMORY_FILES = {"MEMORY.md", "SCHEMA.md", "UPGRADE_PLAN.md"}
THOUGHTS_ARCHIVE_FILE = "thoughts-index.md"
THOUGHTS_ARCHIVE_BATCH_SIZE = 10   # 每次歸檔最舊幾條
ARCHIVE_SUMMARY_MAX_CHARS = 150
MEMORY_INDEX_MAX_LINES = 200       # Claude context 截斷上限（固定值，非可調閾值）


# ── Frontmatter 解析 ──────────────────────────────────────────────

def _parse_frontmatter(content: str) -> dict:
    """從 Markdown frontmatter 解析 valid_until 和 review_by 欄位。"""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {}

    result = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        if key in ("valid_until", "review_by"):
            result[key] = val.strip().strip('"').strip("'")
    return result


def _parse_date(val: str) -> date | None:
    """解析 YYYY-MM-DD 字串，null/None/空字串回傳 None。"""
    if not val or val.lower() in ("null", "none", ""):
        return None
    try:
        return date.fromisoformat(val)
    except ValueError:
        return None


def _set_frontmatter_field(content: str, field: str, value: str) -> str:
    """在 frontmatter 中設定欄位值；欄位不存在則在結尾 --- 前插入。"""
    pattern = re.compile(rf"^({re.escape(field)}\s*:).*$", re.MULTILINE)
    if pattern.search(content):
        return pattern.sub(rf"\1 {value}", content)

    match = re.match(r"^(---\s*\n.*?\n)(---\s*\n)", content, re.DOTALL)
    if match:
        return match.group(1) + f"{field}: {value}\n" + match.group(2) + content[match.end():]
    return content


# ── MEMORY.md 操作 ────────────────────────────────────────────────

def _remove_from_memory_index(index_path: Path, filename: str, dry_run: bool) -> bool:
    """從 MEMORY.md 移除含有 filename 的行，回傳是否有移除。"""
    content = safe_read(index_path)
    if not content:
        return False

    lines = content.splitlines(keepends=True)
    new_lines = [l for l in lines if filename not in l]
    if len(new_lines) == len(lines):
        return False

    if dry_run:
        print(f"  [dry-run] 從 MEMORY.md 移除：{filename}")
        return True

    safe_write(index_path, "".join(new_lines))
    return True


# ── 歸檔操作 ──────────────────────────────────────────────────────

def _archive_file(
    md_path: Path,
    archive_dir: Path,
    index_path: Path,
    today_str: str,
    dry_run: bool,
) -> bool:
    """將 memory 檔案歸檔：設 valid_until、移至 archive/、從 MEMORY.md 移除。
    archive_dir 由呼叫方在執行前建立。
    """
    content = safe_read(md_path)
    if content is None:
        print(f"  [warn] 無法讀取：{md_path.name}", file=sys.stderr)
        return False

    dest = archive_dir / md_path.name
    if dest.exists():
        dest = archive_dir / f"{md_path.stem}_archived_{today_str.replace('-', '')}.md"

    if dry_run:
        print(f"  [dry-run] 歸檔：{md_path.name} → archive/{dest.name}")
        _remove_from_memory_index(index_path, md_path.name, dry_run=True)
        return True

    updated = _set_frontmatter_field(content, "valid_until", today_str)
    try:
        dest.write_text(updated, encoding="utf-8")
        md_path.unlink()
    except OSError as e:
        print(f"  [error] 歸檔失敗 {md_path.name}: {e}", file=sys.stderr)
        return False

    _remove_from_memory_index(index_path, md_path.name, dry_run=False)
    return True


# ── thoughts/ 歸檔 ────────────────────────────────────────────────

def _archive_oldest_thoughts(
    thoughts_dir: Path,
    archive_dir: Path,
    threshold: int,
    dry_run: bool,
) -> int:
    """thoughts/ 超過 threshold 時，歸檔最舊的 THOUGHTS_ARCHIVE_BATCH_SIZE 個。"""
    files = [f for f in thoughts_dir.glob("*.md") if f.is_file()]
    if len(files) <= threshold:
        return 0

    # 只在確定需要歸檔後才排序；用 stem（檔名日期前綴）而非 mtime，
    # 避免雲端同步或換機後 mtime 被重置導致歸檔順序錯亂
    to_archive = sorted(files, key=lambda f: f.stem)[:THOUGHTS_ARCHIVE_BATCH_SIZE]

    if dry_run:
        print(f"  [dry-run] thoughts/ 有 {len(files)} 個（閾值 {threshold}），"
              f"歸檔最舊 {len(to_archive)} 個：")
        for f in to_archive:
            print(f"    - {f.name}")
        return len(to_archive)

    # archive_dir 由呼叫方建立；先確保存在再刪檔，避免刪了檔案卻寫不了 index
    archive_dir.mkdir(parents=True, exist_ok=True)

    archived = 0
    entries = []
    for f in to_archive:
        content = safe_read(f)
        if content is None:
            print(f"  [error] 無法讀取 {f.name}，略過", file=sys.stderr)
            continue
        summary = _extract_first_line(content)
        date_str = f.stem[:10] if len(f.stem) >= 10 else "unknown"
        entries.append(f"\n### [{f.stem}]({f.name}) ({date_str})\n{summary}\n")
        try:
            f.unlink()
            archived += 1
        except OSError as e:
            print(f"  [error] 無法刪除 {f.name}: {e}", file=sys.stderr)

    if entries:
        append_log(archive_dir / THOUGHTS_ARCHIVE_FILE, "".join(entries))

    return archived


def _extract_first_line(content: str) -> str:
    """跳過 frontmatter，回傳第一個非空非標題行；fallback 為第一個非空行。"""
    match = re.match(r"^---\s*\n.*?\n---\s*\n", content, re.DOTALL)
    start = match.end() if match else 0

    fallback = None
    for line in content[start:].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            return stripped[:ARCHIVE_SUMMARY_MAX_CHARS]
        if fallback is None:
            fallback = stripped

    return (fallback[:ARCHIVE_SUMMARY_MAX_CHARS] if fallback else "(無摘要)")


# ── 索引溢出歸檔 ──────────────────────────────────────────────────

_ENTRY_RE = re.compile(r'^\s*-\s+\[.*?\]\(([^)]+)\)')


def _prune_oldest_index_entries(
    memory_dir: Path,
    index_path: Path,
    archive_dir: Path,
    batch_size: int,
    today_str: str,
    dry_run: bool,
) -> int:
    """MEMORY.md 超過閾值時，歸檔最舊的 batch_size 條非 Thoughts 索引條目。

    - ## Thoughts section（及之後）整塊跳過，不列入候選
    - 對應 .md 存在 → _archive_file()；不存在（孤兒行）→ 只刪索引行
    - 回傳實際處理條數
    """
    content = safe_read(index_path)
    if not content:
        return 0

    lines = content.splitlines()

    # 找 ## Thoughts section 起始行（找不到則所有行都是候選範圍）
    thoughts_start = len(lines)
    for i, line in enumerate(lines):
        if re.match(r'^##\s+Thoughts', line.strip()):
            thoughts_start = i
            break

    # 收集 Thoughts section 之前的所有條目行
    candidates: list[str] = []
    for i in range(thoughts_start):
        m = _ENTRY_RE.match(lines[i])
        if m:
            candidates.append(m.group(1))

    to_process = candidates[:batch_size]
    if not to_process:
        return 0

    if dry_run:
        print(f"  [dry-run] 索引溢出歸檔候選（最舊 {len(to_process)} 條）：")
        for path_str in to_process:
            print(f"    - {path_str}")
        return len(to_process)

    processed = 0
    for path_str in to_process:
        md_path = memory_dir / path_str
        filename = Path(path_str).name

        if md_path.exists():
            if _archive_file(md_path, archive_dir, index_path, today_str, dry_run=False):
                processed += 1
            else:
                print(f"  [warn] 歸檔失敗，略過：{path_str}", file=sys.stderr)
        else:
            # 孤兒索引行：對應 .md 不存在，只移除索引行
            print(f"  [warn] 孤兒索引行（檔案不存在），移除：{path_str}")
            if _remove_from_memory_index(index_path, filename, dry_run=False):
                processed += 1

    return processed


# ── 主流程 ────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> int:
    """主流程。回傳 exit code（0=成功，1=失敗/跳過）。"""
    cfg = load_config()
    print(f"[memory_audit] primary_project_dir = {get_path(cfg, 'primary_project_dir')}")

    audit_cfg = cfg.get("memory_audit", {})
    enabled = audit_cfg.get("enabled", False)
    if not enabled:
        print("[memory_audit] enabled: false → 跳過（在 config.yaml 設 enabled: true 啟用）")
        return 0

    auto_archive = audit_cfg.get("auto_archive", True)
    threshold = get_int(cfg, "memory_audit", "thoughts_archive_threshold", default=30)
    warn_lines = get_int(cfg, "memory_audit", "memory_index_warn_lines", default=170)
    prune_threshold = get_int(cfg, "memory_audit", "index_prune_threshold", default=180)
    prune_batch_size = get_int(cfg, "memory_audit", "index_prune_batch_size", default=20)

    # ── 路徑解析 ──────────────────────────────────────────────────
    try:
        memory_dir = get_path(cfg, "memory_dir")
        index_path = get_path(cfg, "memory_index")
    except RuntimeError as e:
        print(f"[memory_audit] 路徑解析失敗：{e}", file=sys.stderr)
        return 1

    if not memory_dir.exists():
        print(f"[memory_audit] memory/ 目錄不存在：{memory_dir}")
        print("  → 執行 setup/setup_memory.bat 初始化，或告訴 Claude「幫我啟用 memory 系統」")
        return 0

    archive_dir = memory_dir / "archive"
    thoughts_dir = memory_dir / "thoughts"
    audit_log = get_path(cfg, "audit_log")
    memory_lock_path = Path(cfg["_root"]) / "data" / "memory.lock"
    today = date.today()
    today_str = today.isoformat()

    lock = nullcontext() if dry_run else FileLock(
        memory_lock_path, timeout=30, stale_timeout=600
    )
    try:
        lock.__enter__()
    except TimeoutError:
        msg = "[memory_audit] memory.lock busy, skipping"
        append_log(audit_log, msg)
        print(msg)
        return 0

    # archive_dir 統一在此建立，_archive_file 不需重複 mkdir
    try:
        if auto_archive and not dry_run:
            archive_dir.mkdir(exist_ok=True)

        print(f"[memory_audit] 開始審計（{today_str}）{'[dry-run]' if dry_run else ''}")

        archived_count = 0
        overdue_items = []
        thoughts_archived = 0

        # ── 1. 掃描 memory/*.md ───────────────────────────────────────
        for md_path in sorted(memory_dir.glob("*.md")):
            if md_path.name in NON_MEMORY_FILES:
                continue

            try:
                content = safe_read(md_path)
                if not content:
                    continue

                fm = _parse_frontmatter(content)

                valid_until = _parse_date(fm.get("valid_until", ""))
                if valid_until is not None and valid_until <= today:
                    if auto_archive:
                        if _archive_file(md_path, archive_dir, index_path, today_str, dry_run):
                            archived_count += 1
                    else:
                        print(f"  [報告] valid_until 已設定（auto_archive=false，略過）：{md_path.name}")

                review_by = _parse_date(fm.get("review_by", ""))
                if review_by is not None and review_by <= today:
                    overdue_items.append((md_path.name, review_by.isoformat()))

            except Exception as e:
                print(f"  [warn] 處理 {md_path.name} 時發生錯誤，略過：{e}", file=sys.stderr)

        # ── 2. thoughts/ 溢出歸檔 ────────────────────────────────────
        if thoughts_dir.exists():
            if auto_archive:
                thoughts_archived = _archive_oldest_thoughts(
                    thoughts_dir, archive_dir, threshold, dry_run
                )
            else:
                count = len(list(thoughts_dir.glob("*.md")))
                if count > threshold:
                    print(f"  [報告] thoughts/ 有 {count} 個，超過閾值 {threshold}"
                          f"（auto_archive=false，略過）")

        # ── 3. MEMORY.md 容量檢查 ────────────────────────────────────
        index_content = safe_read(index_path)
        memory_lines = len(index_content.splitlines()) if index_content else 0

        # ── 3.5. 索引溢出歸檔 ────────────────────────────────────────
        pruned_count = 0
        if memory_lines > prune_threshold:
            if auto_archive:
                pruned_count = _prune_oldest_index_entries(
                    memory_dir, index_path, archive_dir, prune_batch_size, today_str, dry_run
                )
                # 重新讀取行數（歸檔後索引縮短）
                if not dry_run:
                    index_content = safe_read(index_path)
                    memory_lines = len(index_content.splitlines()) if index_content else 0
            else:
                print(f"  [報告] MEMORY.md {memory_lines} 行，超過 {prune_threshold}"
                      f"（auto_archive=false，略過索引歸檔）")

        # ── 4. 輸出摘要 ──────────────────────────────────────────────
        now_str = datetime.now(timezone.utc).isoformat(timespec="seconds")
        nothing_to_do = (not archived_count and not thoughts_archived
                         and not overdue_items and not pruned_count)

        lines = [f"\n[{now_str}] memory_audit 執行摘要{'（dry-run）' if dry_run else ''}"]

        if archived_count:
            lines.append(f"  ✓ valid_until 歸檔：{archived_count} 個移至 archive/")
        if thoughts_archived:
            lines.append(f"  ✓ thoughts/ 歸檔：{thoughts_archived} 個移至 archive/{THOUGHTS_ARCHIVE_FILE}")
        if pruned_count:
            lines.append(f"  ✓ 索引溢出歸檔：{pruned_count} 條移至 archive/")
        if overdue_items:
            lines.append("  ⚠ review_by 到期（需人工確認）：")
            for name, d in overdue_items:
                lines.append(f"      - {name}（到期 {d}）")

        if memory_lines > warn_lines:
            lines.append(f"  ⚠ MEMORY.md：{memory_lines}/{MEMORY_INDEX_MAX_LINES} 行，"
                         f"超過警告閾值 {warn_lines}")
        elif nothing_to_do:
            lines.append(f"  ✓ 無需處理（MEMORY.md：{memory_lines}/{MEMORY_INDEX_MAX_LINES} 行）")
        else:
            lines.append(f"  ℹ MEMORY.md：{memory_lines}/{MEMORY_INDEX_MAX_LINES} 行（正常）")

        summary = "\n".join(lines)
        print(summary)

        if not dry_run:
            append_log(audit_log, summary)
            get_path(cfg, "pending_audit").unlink(missing_ok=True)

        print("[memory_audit] done")
        return 0
    finally:
        lock.__exit__(None, None, None)


# ── CLI 入口 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="memory_audit.py — Claude memory 系統自動維護")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印出會做什麼，不寫入或移動任何檔案")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run))


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
