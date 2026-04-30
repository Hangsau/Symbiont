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
from src.utils.session_reader import parse_session, find_session_by_uuid, find_sessions_after
from src.utils.claude_runner import run_claude, check_auth
from src.utils.file_ops import safe_read, safe_write, append_log, FileLock, rotate_log


# ── 常數 ──────────────────────────────────────────────────────────

EVOLVE_SECTION_TITLE = "## 自動學習規則"   # 用於搜尋（不含換行）
EVOLVE_SECTION_HEADER = EVOLVE_SECTION_TITLE + "\n\n"  # 用於寫入（含換行）
EVOLUTION_LOG_RECENT_DAYS = 14
MAX_EVOLUTION_LOG_TOPICS = 30
MAX_TURN_CHARS = 800        # 單條對話截斷長度（避免單條 turn 佔滿 prompt）
MAX_CLAUDE_MD_CHARS = 3000  # CLAUDE.md 傳入 prompt 的最大字數
MAX_DISTILL_CONTEXT_CHARS = 3000  # 蒸餾 prompt 裡 claude_md_rest 的截斷上限
PROCESSED_RECENT_LIMIT = 50


# ── state.json 操作 ───────────────────────────────────────────────

def _default_state_v2() -> dict:
    return {
        "last_processed_mtime": 0.0,
        "last_processed_uuid": None,
        "processed_recent": [],
        "processed_at": None,
    }


def _backup_legacy_state(state_path: Path, raw: str) -> None:
    backup_path = state_path.with_name(state_path.name + ".pre_v2_backup")
    if backup_path.exists():
        return
    try:
        backup_path.write_text(raw, encoding="utf-8")
    except OSError as e:
        print(f"[evolve] state backup failed (non-critical): {e}", file=sys.stderr)


def _migrate_state_v1_to_v2(data: dict, state_path: Path,
                            sessions_dir: Path | None) -> dict:
    uuid = data.get("last_processed_uuid")
    mtime = 0.0
    if uuid and sessions_dir is not None:
        session_path = find_session_by_uuid(sessions_dir, uuid)
        if session_path:
            try:
                mtime = session_path.stat().st_mtime
            except OSError:
                mtime = 0.0
    migrated = {
        "last_processed_mtime": mtime,
        "last_processed_uuid": uuid,
        "processed_recent": [uuid] if uuid else [],
        "processed_at": data.get("processed_at"),
    }
    safe_write(state_path, json.dumps(migrated, indent=2, ensure_ascii=False))
    return migrated


def _read_state(state_path: Path, sessions_dir: Path | None = None) -> dict:
    raw = safe_read(state_path)
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _default_state_v2()
        if "last_processed_mtime" not in data or "processed_recent" not in data:
            _backup_legacy_state(state_path, raw)
            return _migrate_state_v1_to_v2(data, state_path, sessions_dir)
        state = _default_state_v2()
        state.update(data)
        if not isinstance(state.get("processed_recent"), list):
            state["processed_recent"] = []
        return state
    return _default_state_v2()


def _write_state(state_path: Path, uuid: str, jsonl_path: Path, dry_run: bool) -> None:
    current = _read_state(state_path)
    recent = current.get("processed_recent", [])
    if uuid not in recent:
        recent.append(uuid)
    recent = recent[-PROCESSED_RECENT_LIMIT:]
    try:
        mtime = jsonl_path.stat().st_mtime
    except OSError:
        mtime = current.get("last_processed_mtime", 0.0)
    state = {
        "last_processed_mtime": mtime,
        "last_processed_uuid": uuid,
        "processed_recent": recent,
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

    # fallback：找 cursor 之後最舊、且近期未處理過的 session
    state = _read_state(state_path, sessions_dir)
    candidates = find_sessions_after(
        sessions_dir,
        after_mtime=float(state.get("last_processed_mtime", 0.0) or 0.0),
        after_uuid=state.get("last_processed_uuid"),
        excluded_uuids=set(state.get("processed_recent", [])),
        limit=1,
    )
    if not candidates:
        print("[evolve] 無新 session 需要處理")
        return None, None

    target = candidates[0]
    uuid = target.stem
    print(f"[evolve] new session: {uuid}")
    return target, uuid


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

    # 嘗試從第一個 { 開始用 brace-counting 找完整 JSON 物件
    start = raw.find("{")
    if start != -1:
        depth = 0
        for i, ch in enumerate(raw[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        break

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
        content = rule["content"]
        if not isinstance(content, str) or not content.strip() or not content.startswith("-"):
            return False
    return True


# ── CLAUDE.md 更新 ────────────────────────────────────────────────

def _append_rules_to_claude_md(existing: str, rules: list[dict]) -> str:
    """將新規則追加到 CLAUDE.md 的「自動學習規則」section。
    使用 _find_section_bounds 統一定位邏輯，與 _replace_section_rules 保持一致。
    """
    if not rules:
        return existing

    new_bullets = "\n".join(r["content"] for r in rules)
    bounds = _find_section_bounds(existing)

    if bounds is None:
        # 沒有 section → 追加到末尾
        return (
            existing.rstrip()
            + "\n\n---\n\n"
            + EVOLVE_SECTION_HEADER
            + new_bullets
            + "\n"
        )

    start, end = bounds
    # 取出 section 內現有 bullets（header 之後到 section 結尾），rstrip 去除尾端空行
    existing_bullets = existing[start + len(EVOLVE_SECTION_HEADER):end].rstrip()
    return (
        existing[:start]
        + EVOLVE_SECTION_HEADER
        + existing_bullets
        + "\n"
        + new_bullets
        + "\n"
        + existing[end:]
    )


# ── M6 蒸餾：輔助函式 ────────────────────────────────────────────

def _find_section_bounds(content: str) -> tuple[int, int] | None:
    """回傳 (section_start, section_end)。
    section_start：EVOLVE_SECTION_TITLE 起始位置。
    section_end：下一個 ## section 的 \\n 位置，或檔尾長度。
    section 不存在時回傳 None。
    """
    idx = content.find(EVOLVE_SECTION_TITLE)
    if idx == -1:
        return None
    next_section = content.find("\n## ", idx + len(EVOLVE_SECTION_HEADER))
    end = next_section if next_section != -1 else len(content)
    return idx, end


def _count_section_rules(content: str) -> int:
    """計算 ## 自動學習規則 section 內的 bullet 數。"""
    bounds = _find_section_bounds(content)
    if bounds is None:
        return 0
    start, end = bounds
    return sum(1 for line in content[start:end].splitlines() if line.startswith("- "))


def _extract_section_rules(content: str) -> list[str]:
    """取出 ## 自動學習規則 section 內所有 bullet 的原文。"""
    bounds = _find_section_bounds(content)
    if bounds is None:
        return []
    start, end = bounds
    return [line for line in content[start:end].splitlines() if line.startswith("- ")]


def _extract_claude_md_rest(content: str) -> str:
    """回傳 CLAUDE.md 去掉自動規則 section bullets 後的部分（保留 header）。
    用途：蒸餾 prompt 的去重參考，讓 Claude 知道其他 section 已涵蓋哪些規則。
    """
    bounds = _find_section_bounds(content)
    if bounds is None:
        return content
    start, end = bounds
    header_end = start + len(EVOLVE_SECTION_TITLE)
    return content[:header_end] + "\n" + content[end:]


DISTILL_PROMPT_TEMPLATE = """\
你是 Claude Code 的習慣規則維護系統。現有的「自動學習規則」section 規則數量過多，需要蒸餾整理。

## 任務說明
- 合併語義重疊的規則（保留最具體可執行的版本）
- 移除已被「其他 CLAUDE.md sections」涵蓋的規則（避免重複）
- 保留「遇到 X 做 Y」型規則，移除純觀念型描述
- 將「本次新增規則」融入輸出（不能遺漏）
- 輸出規則數量必須少於輸入總數

## 現有自動規則（{existing_count} 條）
{existing_rules_text}

## 本次新增規則（{new_count} 條）
{new_rules_text}

## CLAUDE.md 其他 sections（去重參考，不輸出這部分）
{claude_md_rest_excerpt}

## 輸出格式（只輸出 JSON，不含任何解釋文字）
```json
{{
  "distilled_rules": [
    {{"content": "- 規則描述（以 - 開頭的 markdown bullet）"}}
  ],
  "merge_summary": "一句話描述做了哪些合併/移除（50 字以內）",
  "removed_count": 3
}}
```
"""


def _build_distill_prompt(existing_rules: list[str], claude_md_rest: str,
                           new_rules: list[dict]) -> str:
    rest_excerpt = claude_md_rest[:MAX_DISTILL_CONTEXT_CHARS] + (
        "\n[...截斷...]" if len(claude_md_rest) > MAX_DISTILL_CONTEXT_CHARS else ""
    )
    return DISTILL_PROMPT_TEMPLATE.format(
        existing_count=len(existing_rules),
        existing_rules_text="\n".join(existing_rules),
        new_count=len(new_rules),
        new_rules_text="\n".join(r["content"] for r in new_rules),
        claude_md_rest_excerpt=rest_excerpt,
    )


def _validate_distill_output(data: dict, existing_count: int, new_count: int) -> bool:
    """驗證蒸餾輸出合法性。四個閘門任一不過 → False（fallback append）。"""
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("distilled_rules"), list):
        return False
    if not isinstance(data.get("merge_summary"), str):
        return False
    if not isinstance(data.get("removed_count"), int):
        return False
    distilled = data["distilled_rules"]
    if len(distilled) < 5:                          # 防止過度裁剪
        return False
    if len(distilled) >= existing_count + new_count:  # 必須有縮減
        return False
    for rule in distilled:
        if not isinstance(rule, dict) or "content" not in rule:
            return False
        if not isinstance(rule["content"], str) or not rule["content"].startswith("- "):
            return False
    return True


def _replace_section_rules(content: str, rules: list[str]) -> str:
    """替換 CLAUDE.md 自動規則 section 的 bullets 為蒸餾後清單。"""
    new_bullets = "\n".join(rules)
    bounds = _find_section_bounds(content)
    if bounds is None:
        return (
            content.rstrip()
            + "\n\n---\n\n"
            + EVOLVE_SECTION_HEADER
            + new_bullets
            + "\n"
        )
    start, end = bounds
    return content[:start] + EVOLVE_SECTION_HEADER + new_bullets + "\n" + content[end:]


def _save_distill_backup(backup_path: Path, existing_rules: list[str], uuid: str) -> None:
    """蒸餾前備份現有規則清單，供使用者手動回滾。"""
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "session": uuid,
        "rules": existing_rules,
    }
    try:
        backup_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"[evolve] distill backup failed (non-critical): {e}", file=sys.stderr)


# ── evolution_log 追加 ────────────────────────────────────────────

def _append_evolution_log(log_path: Path, uuid: str, summary: str,
                           rules_count: int, dry_run: bool,
                           distill_data: dict | None = None,
                           distill_before_count: int = 0) -> None:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if distill_data:
        distilled_count = len(distill_data["distilled_rules"])
        entry = (
            f"\n## {date_str} — [distillation] {distill_data['merge_summary']}\n"
            f"- session: {uuid}\n"
            f"- before: {distill_before_count} rules → after: {distilled_count} rules\n"
            f"- removed: {distill_data['removed_count']}\n"
        )
    else:
        entry = (
            f"\n## {date_str} — {summary}\n"
            f"- session: {uuid}\n"
            f"- rules_added: {rules_count}\n"
        )
    if dry_run:
        print(f"[dry-run] would append to evolution_log:\n{entry}")
    else:
        append_log(log_path, entry)


# ── Synthesis counter ─────────────────────────────────────────────

def _increment_synth_counter(cfg: dict, dry_run: bool) -> None:
    """evolve 每跑一次（含 wrap-skip），計數器 +1。達到 sessions_per_cycle 時背景啟動 synthesize.py。

    使用 FileLock 保護 synth_state.json，避免多個 evolve 進程同時寫入。
    """
    data_dir = Path(cfg["_root"]) / "data"
    state_path = data_dir / "synth_state.json"
    lock_path = data_dir / "synth_state.lock"
    sessions_per_cycle = get_int(cfg, "synthesize", "sessions_per_cycle", default=10)

    try:
        with FileLock(lock_path, timeout=5):
            raw = state_path.read_text(encoding="utf-8") if state_path.exists() else "{}"
            try:
                state = json.loads(raw)
            except json.JSONDecodeError:
                state = {}

            counter = state.get("sessions_since_last_synth", 0) + 1
            state["sessions_since_last_synth"] = counter

            if dry_run:
                print(f"[dry-run] synth counter would be: {counter}/{sessions_per_cycle}")
            else:
                state_path.write_text(
                    json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                print(f"[evolve] synth counter: {counter}/{sessions_per_cycle}")

            if counter >= sessions_per_cycle:
                state["sessions_since_last_synth"] = 0
                if not dry_run:
                    state_path.write_text(
                        json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8"
                    )

                synthesize_path = Path(cfg["_root"]) / "src" / "synthesize.py"
                if not synthesize_path.exists():
                    print("[evolve] synthesize.py not found, skipping", file=sys.stderr)
                    return

                nvm_latest = ""
                nvm_dir = Path.home() / ".nvm" / "versions" / "node"
                if nvm_dir.exists():
                    versions = sorted(nvm_dir.glob("*/bin"), key=lambda p: p.parent.name)
                    if versions:
                        nvm_latest = str(versions[-1])

                extra_paths = ":".join(p for p in [
                    str(Path.home() / "AppData" / "Roaming" / "npm"),
                    str(Path.home() / ".local" / "bin"),
                    "/usr/local/bin", "/opt/homebrew/bin", nvm_latest,
                ] if p)
                env_path = extra_paths + ":" + subprocess.os.environ.get("PATH", "")

                if dry_run:
                    print("[dry-run] would launch synthesize.py in background")
                else:
                    subprocess.Popen(
                        [sys.executable, str(synthesize_path)],
                        cwd=cfg["_root"],
                        env={**subprocess.os.environ, "PATH": env_path},
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    print("[evolve] synthesize.py launched in background")

    except Exception as e:
        print(f"[evolve] synth counter error (non-critical): {e}", file=sys.stderr)


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
    print(f"[evolve] primary_project_dir = {get_path(cfg, 'primary_project_dir')}")
    error_log = get_path(cfg, "error_log")
    rotate_log(error_log, max_lines=2000)
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
        _increment_synth_counter(cfg, dry_run)
        return 0

    # ── 找目標 session ────────────────────────────────────────────
    jsonl_path, uuid = _find_target_session(cfg)
    if jsonl_path is None:
        return 0

    # ── 解析 session ──────────────────────────────────────────────
    turns = parse_session(jsonl_path, max_turns=max_turns)
    if not turns:
        print(f"[evolve] session 無可解析對話：{uuid}")
        _write_state(state_path, uuid, jsonl_path, dry_run)
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

    # ── M6 蒸餾檢查（dry-run 跳過 LLM call）─────────────────────
    distill_threshold = get_int(cfg, "evolve", "distill_threshold", default=25)
    distill_result: dict | None = None
    distill_before_count = 0
    distill_backup_path = get_path(cfg, "state_file").parent / "distill_backup.json"

    if dry_run:
        if rules:
            print("[dry-run] rules that would be added:")
            for r in rules:
                print(f"  {r['content']}")
        else:
            print("[dry-run] no rules to add")
        if distill_threshold > 0 and rules:
            existing_count = _count_section_rules(claude_md)
            if existing_count + len(rules) >= distill_threshold:
                print(f"[dry-run] distillation would trigger: existing={existing_count} + new={len(rules)} >= threshold={distill_threshold}")
            else:
                print(f"[dry-run] distillation not triggered: existing={existing_count} + new={len(rules)} < threshold={distill_threshold}")
        return 0

    if distill_threshold > 0 and rules:
        existing_count = _count_section_rules(claude_md)
        if existing_count + len(rules) >= distill_threshold:
            distill_before_count = existing_count + len(rules)
            print(f"[evolve] distillation triggered: {existing_count}+{len(rules)} >= {distill_threshold}")
            existing_rules = _extract_section_rules(claude_md)
            rest = _extract_claude_md_rest(claude_md)
            distill_prompt = _build_distill_prompt(existing_rules, rest, rules)
            raw_distill = run_claude(distill_prompt, cfg)
            if raw_distill:
                distill_data = _extract_json(raw_distill)
                if distill_data and _validate_distill_output(distill_data, existing_count, len(rules)):
                    distill_result = distill_data
                    print(f"[evolve] distillation ok: {distill_before_count} → {len(distill_data['distilled_rules'])} rules")
                else:
                    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                    append_log(error_log, f"[evolve] [{ts}] distillation validation failed, falling back to append")
                    print("[evolve] distillation failed validation → fallback to append")
            else:
                print("[evolve] distillation LLM call failed → fallback to append")

    # ── 寫入 CLAUDE.md（FileLock 保護）──────────────────────────
    if rules or distill_result:
        lock_path = global_claude_md_path.with_suffix(".lock")
        with FileLock(lock_path, timeout=30):
            # 重新讀取（可能在 lock 等待期間被其他程序更新）
            current_content = safe_read(global_claude_md_path) or ""
            if distill_result:
                _save_distill_backup(distill_backup_path,
                                     _extract_section_rules(current_content), uuid)
                updated = _replace_section_rules(
                    current_content,
                    [r["content"] for r in distill_result["distilled_rules"]]
                )
            else:
                updated = _append_rules_to_claude_md(current_content, rules)
            if not safe_write(global_claude_md_path, updated):
                ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
                append_log(error_log, f"[evolve] [{ts}] failed to write CLAUDE.md")
                return 1
        if distill_result:
            print(f"[evolve] CLAUDE.md distilled: {distill_before_count} → {len(distill_result['distilled_rules'])} rules")
        else:
            print(f"[evolve] CLAUDE.md updated (+{len(rules)} rules)")

    # ── Append evolution_log ──────────────────────────────────────
    _append_evolution_log(evolution_log_path, uuid, summary, len(rules), dry_run,
                          distill_data=distill_result,
                          distill_before_count=distill_before_count)

    # ── 更新 state.json ───────────────────────────────────────────
    _write_state(state_path, uuid, jsonl_path, dry_run)

    # ── 清除 pending_evolve.txt ───────────────────────────────────
    if pending_path.exists():
        pending_path.unlink(missing_ok=True)
        print("[evolve] pending_evolve.txt cleared")

    # ── 備份 ─────────────────────────────────────────────────────
    _run_backup(cfg)

    # ── Synthesis counter ─────────────────────────────────────────
    _increment_synth_counter(cfg, dry_run)

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
