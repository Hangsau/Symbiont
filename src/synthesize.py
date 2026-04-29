"""
synthesize.py — 跨 session 自動進化模組

觸發方式：
  - evolve.py 每跑 N 次後自動背景啟動（N = config synthesize.sessions_per_cycle）
  - 手動：python src/synthesize.py [--dry-run]

流程：
  1. 讀 synth_state.json，找上次 synthesis 後的 sessions
  2. 從每個 session 提取 friction + habit fragments（ctx_cap 控制）
  3. 掃 session JSONL 計算每個 skill 的使用次數
  4. 一次 LLM call：辨識 patterns → 生成 skill content + memories
  5. 寫入 ~/.claude/skills/<topic>/SKILL.md（新建或迭代）
  6. 寫入 memory/thoughts/（洞見）
  7. 依標準差清掃低使用率 skill
  8. 更新 synth_state.json + evolution_log

絕對禁忌：JSON 解析失敗時不寫任何檔案，只記 error.log
"""

import argparse
import json
import re
import statistics
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.utils.config_loader import load_config, get_path, get_int, get_str
from src.utils.session_reader import parse_session, find_sessions_since
from src.utils.friction_extractor import extract_friction_fragments
from src.utils.habit_extractor import extract_habit_fragments
from src.utils.claude_runner import run_claude, check_auth
from src.utils.file_ops import safe_read, safe_write, append_log, FileLock


# ── 常數 ──────────────────────────────────────────────────────────

SYNTH_STATE_KEY = "synthesize"
MAX_TURNS_PER_SESSION = 50


# ── synth_state.json 操作 ─────────────────────────────────────────

def _load_synth_state(state_path: Path) -> dict:
    raw = safe_read(state_path)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {
        "sessions_since_last_synth": 0,
        "last_synth_at": None,
        "last_synth_uuid": None,
        "skill_stats": {},
    }


def _save_synth_state(state_path: Path, state: dict, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would write synth_state: sessions_since={state.get('sessions_since_last_synth')}")
        return
    safe_write(state_path, json.dumps(state, indent=2, ensure_ascii=False))


# ── session 選取 ──────────────────────────────────────────────────

def _find_target_sessions(cfg: dict, state: dict) -> list[Path]:
    """回傳上次 synthesis 之後的 sessions，最多 sessions_per_cycle 個。"""
    sessions_dir = get_path(cfg, "sessions_dir")
    limit = get_int(cfg, SYNTH_STATE_KEY, "sessions_per_cycle", default=10)
    after_ts = 0.0
    if state.get("last_synth_at"):
        try:
            dt = datetime.fromisoformat(state["last_synth_at"])
            after_ts = dt.timestamp()
        except (ValueError, TypeError):
            after_ts = 0.0
    return find_sessions_since(sessions_dir, after_ts, limit)


# ── skill 使用次數掃描 ────────────────────────────────────────────

def _scan_skill_usages(sessions: list[Path]) -> dict[str, int]:
    """從 session JSONL 掃描 Skill("xxx") 呼叫，統計每個 skill 的使用次數。"""
    pattern = re.compile(r'Skill\(["\']([^"\']+)["\']\)')
    counts: dict[str, int] = {}
    for path in sessions:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in pattern.finditer(text):
            name = match.group(1)
            counts[name] = counts.get(name, 0) + 1
    return counts


# ── fragment 提取與組裝 ───────────────────────────────────────────

def _extract_all_fragments(sessions: list[Path], cfg: dict) -> tuple[str, str]:
    """從所有 sessions 提取 friction + habit fragments，遵守 ctx_cap。"""
    ctx_cap = get_int(cfg, SYNTH_STATE_KEY, "ctx_cap_chars", default=12000)
    fric_per = get_int(cfg, SYNTH_STATE_KEY, "friction_per_session", default=1500)
    habit_per = get_int(cfg, SYNTH_STATE_KEY, "habit_per_session", default=800)

    all_friction: list[str] = []
    all_habit: list[str] = []
    total = 0

    for path in sessions:
        if total >= ctx_cap:
            break
        turns = parse_session(path, max_turns=MAX_TURNS_PER_SESSION)
        if not turns:
            continue

        fric = extract_friction_fragments(turns, fric_per)
        habit = extract_habit_fragments(turns, habit_per)

        session_label = f"\n--- session: {path.stem[:8]} ---\n"
        if fric:
            chunk = session_label + fric
            if total + len(chunk) <= ctx_cap:
                all_friction.append(chunk)
                total += len(chunk)
        if habit:
            chunk = session_label + habit
            if total + len(chunk) <= ctx_cap:
                all_habit.append(chunk)
                total += len(chunk)

    return "\n".join(all_friction), "\n".join(all_habit)


# ── LLM Prompt ────────────────────────────────────────────────────

SYNTHESIS_PROMPT = """\
你是 Claude Code 的跨 session 行為進化系統。分析以下多個 session 的摩擦片段和習慣片段，識別 recurring patterns，為每個 pattern 生成對應的 skill。

## 規則
- 只識別出現在 {min_evidence} 個以上 session 的 pattern（單次偶發不算）
- 區分三種 skill 類型：
  - guard：防止慣性錯誤（來自摩擦片段）
  - workflow：標準化重複流程（來自習慣片段）
  - audit：完成後的品質驗收（來自習慣片段）
- skill_content 欄位中的換行必須寫成 \\n（不能有真正的換行符號）
- 若無足夠 pattern，patterns 回傳空陣列

## 摩擦片段（Guard skill 原料）
{friction_text}

## 習慣片段（Workflow/Audit skill 原料）
{habit_text}

## 已知 skill topics（避免重複命名）
{known_topics}

## 輸出格式（只輸出 JSON，不含任何解釋文字）
```json
{{
  "patterns": [
    {{
      "topic": "kebab-case-topic-name",
      "pattern_type": "guard",
      "evidence_sessions": 4,
      "root_cause": "execution-forget",
      "skill_content": "---\\nname: topic-name\\ndescription: 一句話說明\\ntrigger: /topic-name\\ntype: guard\\nauto_generated: true\\niteration: 1\\n---\\n\\n具體的 skill 內容..."
    }}
  ],
  "memories": [
    {{
      "filename": "feedback_xxx.md",
      "content": "---\\nname: xxx\\ndescription: xxx\\ntype: feedback\\ncreated: {today}\\nvalid_until: null\\nsuperseded_by: null\\n---\\n\\n記憶內容..."
    }}
  ],
  "synthesis_summary": "一句話描述本次 synthesis 的主要發現"
}}
```
"""


def _build_synthesis_prompt(friction: str, habit: str, known_topics: list[str],
                            min_evidence: int = 3) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    topics_str = ", ".join(known_topics) if known_topics else "（無）"
    friction_str = friction if friction else "（本次 sessions 無摩擦片段）"
    habit_str = habit if habit else "（本次 sessions 無習慣片段）"
    return SYNTHESIS_PROMPT.format(
        friction_text=friction_str,
        habit_text=habit_str,
        known_topics=topics_str,
        today=today,
        min_evidence=min_evidence,
    )


# ── JSON 解析 ─────────────────────────────────────────────────────

def _parse_synthesis_output(raw: str) -> dict | None:
    """從 LLM 輸出提取 JSON，失敗回傳 None。"""
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
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
                        return json.loads(raw[start:i + 1])
                    except json.JSONDecodeError:
                        break
    return None


def _validate_synthesis_output(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("patterns"), list):
        return False
    if not isinstance(data.get("memories"), list):
        return False
    if not isinstance(data.get("synthesis_summary"), str):
        return False
    for p in data["patterns"]:
        if not isinstance(p, dict):
            return False
        for field in ("topic", "pattern_type", "skill_content"):
            if not isinstance(p.get(field), str):
                return False
    return True


# ── Skill 寫入 ────────────────────────────────────────────────────

def _write_skill(topic: str, skill_content: str, skills_dir: Path,
                 iteration: int, dry_run: bool) -> bool:
    """將 skill_content 寫入 skills_dir/<topic>/SKILL.md。

    skill_content 中的 \\n（字面兩字元）先還原為真正換行。
    iteration 欄位更新為當前值。
    """
    content = skill_content.replace("\\n", "\n")
    # 更新 iteration 欄位
    content = re.sub(r"(iteration:\s*)\d+", f"\\g<1>{iteration}", content)

    skill_dir = skills_dir / topic
    skill_path = skill_dir / "SKILL.md"

    if dry_run:
        print(f"[dry-run] would write skill: {skill_path}")
        print(f"  content preview: {content[:200].replace(chr(10), ' ')}")
        return True

    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content, encoding="utf-8")
        print(f"[synthesize] skill written: {topic} (iteration {iteration})")
        return True
    except OSError as e:
        print(f"[synthesize] failed to write skill {topic}: {e}", file=sys.stderr)
        return False


# ── Memory 寫入 ───────────────────────────────────────────────────

def _write_memories(memories: list[dict], memory_dir: Path,
                    memory_index: Path, dry_run: bool) -> None:
    """將 memories 寫入 memory/thoughts/ 並更新 MEMORY.md 索引。"""
    if not memories:
        return

    thoughts_dir = memory_dir / "thoughts"

    for mem in memories:
        filename = mem.get("filename", "")
        content = mem.get("content", "").replace("\\n", "\n")
        if not filename or not content:
            continue

        mem_path = thoughts_dir / filename

        if dry_run:
            print(f"[dry-run] would write memory: {mem_path.name}")
            continue

        try:
            thoughts_dir.mkdir(parents=True, exist_ok=True)
            mem_path.write_text(content, encoding="utf-8")
            print(f"[synthesize] memory written: {filename}")
        except OSError as e:
            print(f"[synthesize] failed to write memory {filename}: {e}", file=sys.stderr)
            continue

        # 更新 MEMORY.md 索引
        name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
        if name_match and desc_match:
            name = name_match.group(1).strip()
            desc = desc_match.group(1).strip()
            index_line = f"- [{name}](thoughts/{filename}) — {desc}\n"
            try:
                existing = memory_index.read_text(encoding="utf-8") if memory_index.exists() else ""
                if filename not in existing:
                    with memory_index.open("a", encoding="utf-8") as f:
                        f.write(index_line)
            except OSError as e:
                print(f"[synthesize] failed to update memory index: {e}", file=sys.stderr)


# ── Skill 使用率統計與清掃 ────────────────────────────────────────

def _update_skill_stats(stats: dict, current_usages: dict[str, int],
                        cfg: dict, skills_dir: Path, dry_run: bool) -> list[str]:
    """更新 skill_stats，依標準差清掃低使用率 skill。回傳被刪的 topic 列表。"""
    stdev_mult = float(get_str(cfg, SYNTH_STATE_KEY, "skill_stdev_multiplier", default="2.0"))
    low_cycles_limit = get_int(cfg, SYNTH_STATE_KEY, "skill_low_cycles_to_delete", default=2)

    # 合併所有已知 skill（stats 裡有的 + 本次掃到的）
    all_topics = set(stats.keys()) | set(current_usages.keys())

    for topic in all_topics:
        if topic not in stats:
            stats[topic] = {"cycle_usages": [], "low_count": 0, "status": "active"}
        stats[topic]["cycle_usages"].append(current_usages.get(topic, 0))

    # 計算標準差
    usages_this_cycle = [current_usages.get(t, 0) for t in all_topics]
    deleted: list[str] = []

    if len(usages_this_cycle) < 2:
        return deleted  # 資料不足，不做任何清掃

    try:
        mean = statistics.mean(usages_this_cycle)
        stdev = statistics.stdev(usages_this_cycle)
    except statistics.StatisticsError:
        return deleted

    if stdev == 0:
        return deleted  # 全部一樣，不清掃

    threshold = mean - stdev_mult * stdev

    for topic in list(all_topics):
        stat = stats.get(topic, {})
        this_usage = current_usages.get(topic, 0)
        cycles = stat.get("cycle_usages", [])
        growing = len(cycles) >= 2 and cycles[-1] >= cycles[-2]

        if this_usage < threshold and not growing:
            stat["low_count"] = stat.get("low_count", 0) + 1
            if stat["low_count"] >= low_cycles_limit:
                skill_path = skills_dir / topic / "SKILL.md"
                if dry_run:
                    print(f"[dry-run] would delete low-usage skill: {topic}")
                else:
                    try:
                        if skill_path.exists():
                            skill_path.unlink()
                            skill_dir = skills_dir / topic
                            if skill_dir.exists() and not any(skill_dir.iterdir()):
                                skill_dir.rmdir()
                        deleted.append(topic)
                        del stats[topic]
                        print(f"[synthesize] deleted low-usage skill: {topic}")
                    except OSError as e:
                        print(f"[synthesize] failed to delete {topic}: {e}", file=sys.stderr)
        else:
            stat["low_count"] = 0

    return deleted


# ── Evolution log ─────────────────────────────────────────────────

def _append_evolution_log(log_path: Path, summary: str,
                          created: list[str], deleted: list[str],
                          dry_run: bool) -> None:
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry = (
        f"\n## {date_str} — [synthesis] {summary}\n"
        f"- skills_created: {', '.join(created) if created else 'none'}\n"
        f"- skills_deleted: {', '.join(deleted) if deleted else 'none'}\n"
    )
    if dry_run:
        print(f"[dry-run] would append to evolution_log:\n{entry}")
    else:
        append_log(log_path, entry)


# ── 主流程 ────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> int:
    cfg = load_config()
    error_log = get_path(cfg, "error_log")

    if not check_auth():
        msg = "auth check failed: ~/.claude/.credentials.json not found"
        append_log(error_log, f"[synthesize] {msg}")
        print(f"[synthesize] {msg}", file=sys.stderr)
        return 1

    # ── 路徑設定 ──────────────────────────────────────────────────
    data_dir = Path(cfg["_root"]) / "data"
    state_path = data_dir / "synth_state.json"
    evolution_log_path = get_path(cfg, "evolution_log")
    memory_dir = get_path(cfg, "memory_dir")
    memory_index = get_path(cfg, "memory_index")
    skills_dir = Path.home() / ".claude" / "skills"

    # ── synth_state 讀取 ──────────────────────────────────────────
    state = _load_synth_state(state_path)

    # ── session 選取 ──────────────────────────────────────────────
    sessions = _find_target_sessions(cfg, state)
    print(f"[synthesize] found {len(sessions)} sessions to analyze")
    if dry_run:
        for s in sessions:
            print(f"  - {s.stem[:16]}")

    if not sessions:
        print("[synthesize] no new sessions, skipping")
        return 0

    # ── fragment 提取 ─────────────────────────────────────────────
    friction_text, habit_text = _extract_all_fragments(sessions, cfg)
    total_chars = len(friction_text) + len(habit_text)
    print(f"[synthesize] fragments: friction={len(friction_text)}c, habit={len(habit_text)}c, total={total_chars}c")

    # ── skill 使用次數掃描 ────────────────────────────────────────
    skill_usages = _scan_skill_usages(sessions)
    print(f"[synthesize] skill usages this cycle: {skill_usages}")

    # ── 組 prompt → LLM ──────────────────────────────────────────
    known_topics = list(state.get("skill_stats", {}).keys())
    min_evidence = get_int(cfg, SYNTH_STATE_KEY, "min_evidence_sessions", default=3)
    prompt = _build_synthesis_prompt(friction_text, habit_text, known_topics, min_evidence)

    if dry_run:
        print("[dry-run] prompt preview (first 600 chars):")
        print(prompt[:600])
        print("...\n[dry-run] skipping LLM call")
        # 模擬空輸出
        parsed = {"patterns": [], "memories": [], "synthesis_summary": "[dry-run]"}
    else:
        print("[synthesize] calling claude -p ...")
        raw_output = run_claude(prompt, cfg)
        if raw_output is None:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            append_log(error_log, f"[synthesize] [{ts}] LLM call failed")
            return 1

        parsed = _parse_synthesis_output(raw_output)
        if parsed is None or not _validate_synthesis_output(parsed):
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            msg = f"[synthesize] [{ts}] JSON parse/validate failed. raw:\n{(raw_output or '')[:500]}"
            append_log(error_log, msg)
            print("[synthesize] JSON parse failed → error.log only", file=sys.stderr)
            return 1

    patterns = parsed.get("patterns", [])
    memories = parsed.get("memories", [])
    summary = parsed.get("synthesis_summary", "")
    print(f"[synthesize] patterns={len(patterns)}, memories={len(memories)}, summary={summary}")

    # ── Skill 寫入 ────────────────────────────────────────────────
    skill_stats = state.setdefault("skill_stats", {})
    created_skills: list[str] = []

    for pattern in patterns:
        topic = pattern.get("topic", "").strip()
        skill_content = pattern.get("skill_content", "").strip()
        if not topic or not skill_content:
            continue

        # 計算 iteration
        existing_stat = skill_stats.get(topic, {})
        iteration = len(existing_stat.get("cycle_usages", [])) + 1

        if _write_skill(topic, skill_content, skills_dir, iteration, dry_run):
            created_skills.append(topic)
            if topic not in skill_stats:
                skill_stats[topic] = {"cycle_usages": [], "low_count": 0, "status": "active"}

    # ── Memory 寫入 ───────────────────────────────────────────────
    _write_memories(memories, memory_dir, memory_index, dry_run)

    # ── Skill 使用率更新與清掃 ────────────────────────────────────
    deleted_skills = _update_skill_stats(skill_stats, skill_usages, cfg, skills_dir, dry_run)

    # ── Evolution log ─────────────────────────────────────────────
    _append_evolution_log(evolution_log_path, summary, created_skills, deleted_skills, dry_run)

    # ── synth_state 更新 ──────────────────────────────────────────
    state["sessions_since_last_synth"] = 0
    state["last_synth_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if sessions:
        state["last_synth_uuid"] = sessions[-1].stem
    _save_synth_state(state_path, state, dry_run)

    print(f"[synthesize] done. created={len(created_skills)}, deleted={len(deleted_skills)}")
    return 0


# ── CLI 入口 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="synthesize.py — cross-session skill synthesis")
    parser.add_argument("--dry-run", action="store_true",
                        help="分析並印出結果，不寫入任何檔案")
    args = parser.parse_args()
    sys.exit(run(dry_run=args.dry_run))


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
