"""
evolve.py — 分析最新 Claude Code session，萃取習慣規則，更新全域 CLAUDE.md

觸發方式：
  - Stop hook → 背景呼叫（延遲 30 秒）
  - Task Scheduler → 開機補跑（pending_evolve.txt 存在時）
  - 手動：python src/evolve.py [--dry-run] [--skip-if-wrap-done]

絕對禁忌：JSON 解析失敗時不寫任何檔案，只記 error.log
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config, get_path, get_int, get_str
from src.utils.session_reader import parse_session, find_latest_session, find_session_by_uuid
from src.utils.claude_runner import run_claude, check_auth
from src.utils.file_ops import safe_read, safe_write, append_log, FileLock


# ── 常數 ──────────────────────────────────────────────────────────

EVOLVE_SECTION_TITLE = "## 自動學習規則"   # 用於搜尋（不含換行）
EVOLVE_SECTION_HEADER = EVOLVE_SECTION_TITLE + "\n\n"  # 用於寫入（含換行）
EVOLUTION_LOG_RECENT_DAYS = 14
MAX_EVOLUTION_LOG_TOPICS = 30
MAX_TURN_CHARS = 800       # 單條對話截斷長度（避免單條 turn 佔滿 prompt）
MAX_CLAUDE_MD_CHARS = 3000  # CLAUDE.md 傳入 prompt 的最大字數


# ── state.json 操作 ───────────────────────────────────────────────

def _read_state(state_path: Path) -> dict:
    raw = safe_read(state_path)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {"last_processed_uuid": None, "processed_at": None}


def _write_state(state_path: Path, uuid: str, dry_run: bool) -> None:
    state = {
        "last_processed_uuid": uuid,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if dry_run:
        print(f"[dry-run] would write state.json: {state}")
    else:
        safe_write(state_path, json.dumps(state, indent=2, ensure_ascii=False))


# ── session 選擇 ──────────────────────────────────────────────────

def _find_target_session(cfg: dict) -> tuple[Path | None, str | None]:
    """決定要處理的 session。回傳 (jsonl_path, uuid)。"""
    pending_path = get_path(cfg, "pending_evolve")
    sessions_dir = get_path(cfg, "sessions_dir")
    state_path = get_path(cfg, "state_file")

    # 優先：pending_evolve.txt 指定的 uuid
    if pending_path.exists():
        uuid = pending_path.read_text(encoding="utf-8").strip()
        if uuid:
            p = find_session_by_uuid(sessions_dir, uuid)
            if p:
                print(f"[evolve] pending session: {uuid}")
                return p, uuid
            print(f"[evolve] pending uuid not found: {uuid}", file=sys.stderr)
            pending_path.unlink(missing_ok=True)  # 無效 uuid → 清掉，繼續找最新

    # fallback：找比 last_processed_uuid 更新的 session
    state = _read_state(state_path)
    last_uuid = state.get("last_processed_uuid")

    latest = find_latest_session(sessions_dir)
    if latest is None:
        return None, None

    latest_uuid = latest.stem
    if latest_uuid == last_uuid:
        print("[evolve] 無新 session 需要處理")
        return None, None

    print(f"[evolve] new session: {latest_uuid}")
    return latest, latest_uuid


# ── evolution_log 讀取（只取 canonical topics）──────────────────

def _read_evolution_log_topics(log_path: Path) -> str:
    """讀取 evolution_log.md，只回傳最近 14 天條目的摘要（節省 prompt 長度）。"""
    raw = safe_read(log_path)
    if not raw:
        return "(尚無 evolution log)"

    lines = raw.splitlines()
    # 找最近 EVOLUTION_LOG_RECENT_DAYS 天的 ## 條目
    recent = []
    today = datetime.now(timezone.utc)
    for line in lines:
        if line.startswith("## ") and "—" in line:
            # 格式：## YYYY-MM-DD — summary
            parts = line[3:].split("—", 1)
            try:
                entry_date = datetime.fromisoformat(parts[0].strip())
                if (today.date() - entry_date.date()).days <= EVOLUTION_LOG_RECENT_DAYS:
                    recent.append(line.strip())
            except ValueError:
                recent.append(line.strip())

    if not recent:
        return "(最近無 evolution 記錄)"

    # 只取最近 MAX_EVOLUTION_LOG_TOPICS 條
    return "\n".join(recent[-MAX_EVOLUTION_LOG_TOPICS:])


# ── 格式化 session turns 為 prompt 用文字 ────────────────────────

def _format_turns(turns: list[dict]) -> str:
    parts = []
    for t in turns:
        role = "USER" if t["role"] == "user" else "ASSISTANT"
        content = t["content"][:MAX_TURN_CHARS]
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


# ── Prompt 組裝 ───────────────────────────────────────────────────

PROMPT_TEMPLATE = """\
你是 Claude Code 的習慣學習系統。分析以下 session，找出值得寫入 CLAUDE.md 的行為規則。

## 任務說明
- 只萃取**反覆出現**或**明確被糾正**的行為模式
- 不重複 CLAUDE.md 裡已有的規則
- 每條規則必須具體可執行（不是觀念，是「遇到 X 做 Y」）
- 若無值得學習的新規則，rules_to_add 回傳空陣列

## 現有 CLAUDE.md 規則摘要
{claude_md_excerpt}

## 近期 Evolution Log
{evolution_log_topics}

## Session 對話
{session_text}

## 輸出格式（只輸出 JSON，不含任何解釋文字）
```json
{{
  "rules_to_add": [
    {{"content": "- 規則描述（以 - 開頭的 markdown bullet）"}}
  ],
  "summary": "一句話描述此 session 的主要學習（50 字以內）"
}}
```
"""


def _build_prompt(turns: list[dict], claude_md: str, evolution_topics: str) -> str:
    claude_excerpt = claude_md[:MAX_CLAUDE_MD_CHARS] + ("\n[...截斷...]" if len(claude_md) > MAX_CLAUDE_MD_CHARS else "")
    session_text = _format_turns(turns)
    return PROMPT_TEMPLATE.format(
        claude_md_excerpt=claude_excerpt,
        evolution_log_topics=evolution_topics,
        session_text=session_text,
    )


# ── JSON 解析（含 markdown code fence 處理）──────────────────────

def _extract_json(raw: str) -> dict | None:
    """從 LLM 輸出中提取 JSON，容錯 markdown code fence。"""
    # 嘗試直接解析
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass

    # 嘗試從 ```json ... ``` 中提取
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 嘗試找第一個 { ... } 塊
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _validate_output(data: dict) -> bool:
    """驗證 LLM 輸出結構符合 schema。"""
    if not isinstance(data, dict):
        return False
    if "rules_to_add" not in data or not isinstance(data["rules_to_add"], list):
        return False
    if "summary" not in data or not isinstance(data["summary"], str):
        return False
    for rule in data["rules_to_add"]:
        if not isinstance(rule, dict) or "content" not in rule:
            return False
    return True


# ── CLAUDE.md 更新 ────────────────────────────────────────────────

def _append_rules_to_claude_md(existing: str, rules: list[dict]) -> str:
    """將新規則追加到 CLAUDE.md 的「自動學習規則」section。"""
    if not rules:
        return existing

    new_bullets = "\n".join(r["content"] for r in rules)

    if EVOLVE_SECTION_TITLE in existing:
        # 已有 section → 在 section 末尾插入
        idx = existing.index(EVOLVE_SECTION_TITLE)
        # 找下一個 ## section 或檔案結尾（從 header 結尾後開始搜尋）
        next_section = existing.find("\n## ", idx + len(EVOLVE_SECTION_HEADER))
        if next_section == -1:
            return existing.rstrip() + "\n" + new_bullets + "\n"
        else:
            return (
                existing[:next_section].rstrip()
                + "\n"
                + new_bullets
                + "\n"
                + existing[next_section:]
            )
    else:
        # 沒有 section → 追加到末尾
        return (
            existing.rstrip()
            + "\n\n---\n\n"
            + EVOLVE_SECTION_HEADER
            + new_bullets
            + "\n"
        )


# ── evolution_log 追加 ────────────────────────────────────────────

def _append_evolution_log(log_path: Path, uuid: str, summary: str,
                           rules_count: int, dry_run: bool) -> None:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = (
        f"\n## {date_str} — {summary}\n"
        f"- session: {uuid}\n"
        f"- rules_added: {rules_count}\n"
    )
    if dry_run:
        print(f"[dry-run] would append to evolution_log:\n{entry}")
    else:
        append_log(log_path, entry)


# ── 備份 ──────────────────────────────────────────────────────────

def _run_backup(cfg: dict) -> None:
    backup_dir_raw = get_str(cfg, "paths", "backup_dir", default="").strip()
    if not backup_dir_raw:
        return

    backup_dir = Path(backup_dir_raw).expanduser()
    claude_home = Path.home() / ".claude"

    if sys.platform == "win32":
        cmd = ["robocopy", str(claude_home), str(backup_dir / ".claude"),
               "/MIR", "/NFL", "/NDL", "/NJH", "/NJS"]
    else:
        cmd = ["rsync", "-a", "--delete", f"{claude_home}/", str(backup_dir / ".claude")]

    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except Exception as e:
        print(f"[evolve] backup failed: {e}", file=sys.stderr)


# ── 主流程 ────────────────────────────────────────────────────────

def run(dry_run: bool = False, skip_if_wrap_done: bool = False) -> int:
    """主流程。回傳 exit code（0=成功，1=失敗/無需處理）。"""
    cfg = load_config()
    error_log = get_path(cfg, "error_log")
    state_path = get_path(cfg, "state_file")
    pending_path = get_path(cfg, "pending_evolve")
    evolution_log_path = get_path(cfg, "evolution_log")
    global_claude_md_path = get_path(cfg, "global_claude_md")
    wrap_done_path = get_path(cfg, "wrap_done_file")
    max_turns = get_int(cfg, "session_reader", "max_turns", default=50)

    # ── auth 檢查 ─────────────────────────────────────────────────
    if not check_auth():
        msg = "auth check failed: ~/.claude/.credentials.json not found"
        append_log(error_log, f"[evolve] {msg}")
        print(f"[evolve] {msg}", file=sys.stderr)
        return 1

    # ── --skip-if-wrap-done ───────────────────────────────────────
    if skip_if_wrap_done and wrap_done_path.exists():
        print("[evolve] wrap_done detected → skip (wrap already handled this session)")
        if not dry_run:
            wrap_done_path.unlink(missing_ok=True)
        return 0

    # ── 找目標 session ────────────────────────────────────────────
    jsonl_path, uuid = _find_target_session(cfg)
    if jsonl_path is None:
        return 0

    # ── 解析 session ──────────────────────────────────────────────
    turns = parse_session(jsonl_path, max_turns=max_turns)
    if not turns:
        print(f"[evolve] session 無可解析對話：{uuid}")
        _write_state(state_path, uuid, dry_run)
        return 0

    print(f"[evolve] parsed {len(turns)} turns from {uuid}")

    # ── 讀 CLAUDE.md + evolution_log ──────────────────────────────
    claude_md = safe_read(global_claude_md_path) or ""
    evolution_topics = _read_evolution_log_topics(evolution_log_path)

    # ── 組 prompt → claude -p ─────────────────────────────────────
    prompt = _build_prompt(turns, claude_md, evolution_topics)
    print("[evolve] calling claude -p ...")

    if dry_run:
        print("[dry-run] prompt preview (first 500 chars):")
        print(prompt[:500])
        print("...\n[dry-run] skipping actual LLM call")
        # 模擬空輸出
        parsed = {"rules_to_add": [], "summary": "[dry-run] no actual analysis"}
    else:
        raw_output = run_claude(prompt, cfg)
        if raw_output is None:
            # run_claude 已記 error.log
            return 1

        parsed = _extract_json(raw_output)
        if parsed is None:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            msg = f"[{ts}] JSON parse failed for session {uuid}. raw output:\n{raw_output[:500]}"
            append_log(error_log, f"[evolve] {msg}")
            print(f"[evolve] JSON parse failed → error.log only", file=sys.stderr)
            return 1

        if not _validate_output(parsed):
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            msg = f"[{ts}] schema validation failed for session {uuid}. parsed:\n{parsed}"
            append_log(error_log, f"[evolve] {msg}")
            print("[evolve] schema validation failed → error.log only", file=sys.stderr)
            return 1

    rules = parsed.get("rules_to_add", [])
    summary = parsed.get("summary", "(no summary)")
    print(f"[evolve] rules_to_add: {len(rules)}, summary: {summary}")

    if dry_run:
        if rules:
            print("[dry-run] rules that would be added:")
            for r in rules:
                print(f"  {r['content']}")
        else:
            print("[dry-run] no rules to add")
        return 0

    # ── 寫入 CLAUDE.md（FileLock 保護）──────────────────────────
    if rules:
        lock_path = global_claude_md_path.with_suffix(".lock")
        with FileLock(lock_path, timeout=30):
            # 重新讀取（可能在 lock 等待期間被其他程序更新）
            current_content = safe_read(global_claude_md_path) or ""
            updated = _append_rules_to_claude_md(current_content, rules)
            if not safe_write(global_claude_md_path, updated):
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                append_log(error_log, f"[evolve] [{ts}] failed to write CLAUDE.md")
                return 1
        print(f"[evolve] CLAUDE.md updated (+{len(rules)} rules)")

    # ── Append evolution_log ──────────────────────────────────────
    _append_evolution_log(evolution_log_path, uuid, summary, len(rules), dry_run)

    # ── 更新 state.json ───────────────────────────────────────────
    _write_state(state_path, uuid, dry_run)

    # ── 清除 pending_evolve.txt ───────────────────────────────────
    if pending_path.exists():
        pending_path.unlink(missing_ok=True)
        print("[evolve] pending_evolve.txt cleared")

    # ── 備份 ─────────────────────────────────────────────────────
    _run_backup(cfg)

    print("[evolve] done")
    return 0


# ── CLI 入口 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="evolve.py — Claude session habit extractor")
    parser.add_argument("--dry-run", action="store_true",
                        help="分析並印出結果，不寫入任何檔案")
    parser.add_argument("--skip-if-wrap-done", action="store_true",
                        help="若 ~/.claude/.wrap_done.txt 存在則跳過（防止與 wrap skill 重複）")
    args = parser.parse_args()

    sys.exit(run(dry_run=args.dry_run, skip_if_wrap_done=args.skip_if_wrap_done))


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
