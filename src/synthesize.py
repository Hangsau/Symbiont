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
from contextlib import nullcontext
import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.modules.setdefault("synthesize", sys.modules[__name__])

from src.utils.config_loader import load_config, get_path, get_int, get_str
from src.utils.session_reader import parse_session, find_session_by_uuid, find_sessions_after
from src.utils.friction_extractor import extract_friction_fragments
from src.utils.habit_extractor import extract_habit_fragments
from src.utils.claude_runner import run_claude, check_auth
from src.utils.file_ops import safe_read, safe_write, append_log, FileLock
from src.utils.knowledge_writer import (
    write_knowledge_entry, update_knowledge_tags, move_to_distilled
)


# ── 常數 ──────────────────────────────────────────────────────────

SYNTH_STATE_KEY = "synthesize"
KB_KEY = "knowledge_base"
MAX_TURNS_PER_SESSION = 50
_EXISTING_SKILL_MAX = 30           # 最多讀幾個現有 skill
_EXISTING_SKILL_DESC_CHARS = 80    # 每條 description 截斷字數
_QUALITY_SCORE_MIN = 2             # 低於此分數的 skill 不寫入
_EXISTING_FILE_READ_CHARS = 500    # 蒸餾時讀現有 knowledge 每檔上限
_RAW_FILE_READ_CHARS = 600         # 蒸餾時讀原始 memory 每檔上限

# 類型前綴 → knowledge/ 子目錄
_TYPE_MAP = {
    "feedback": "feedback",
    "project": "project",
    "reference": "reference",
    "user": "user",
    "reflection": "thoughts",
}

_SAFE_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}\.md$")
_SAFE_TOPIC_RE = re.compile(r"^[a-z][a-z0-9-]{0,60}$")


def _is_safe_filename(s: str) -> bool:
    return isinstance(s, str) and bool(_SAFE_FILENAME_RE.match(s))


def _is_safe_topic(s: str) -> bool:
    return isinstance(s, str) and bool(_SAFE_TOPIC_RE.match(s))


def _has_required_frontmatter(content: str, required: tuple[str, ...]) -> bool:
    if not isinstance(content, str):
        return False
    content = content.replace("\\n", "\n")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not m:
        return False
    fm = m.group(1)
    return all(re.search(rf"^{k}:\s*\S", fm, re.MULTILINE) for k in required)


# ── 現有 skill 描述載入 ───────────────────────────────────────────

def _load_existing_skill_descriptions(skills_dir: Path) -> str:
    """掃 skills_dir/*/SKILL.md，提取 name + description，供 prompt 判斷重複。"""
    if not skills_dir.exists():
        return "（無現有 skill）"
    lines: list[str] = []
    for skill_path in sorted(skills_dir.glob("*/SKILL.md")):
        try:
            text = skill_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        name_match = re.search(r"^name:\s*(.+)$", text, re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", text, re.MULTILINE)
        if name_match and desc_match:
            name = name_match.group(1).strip()
            desc = desc_match.group(1).strip()[:_EXISTING_SKILL_DESC_CHARS]
            lines.append(f"- {name}: {desc}")
        if len(lines) >= _EXISTING_SKILL_MAX:
            break
    return "\n".join(lines) if lines else "（無現有 skill）"


# ── synth_state.json 操作 ─────────────────────────────────────────

def _default_synth_state_v2() -> dict:
    return {
        "sessions_since_last_synth": 0,
        "last_synth_session_mtime": 0.0,
        "last_synth_session_uuid": None,
        "current_run_id": None,
        "current_run_summary": "",
        "current_run_memories": [],
        "current_run_created_skills": [],
        "current_run_deleted_skills": [],
        "patterns_done_at": None,
        "memories_done_at": None,
        "distill_done_at": None,
        "prune_done_at": None,
        "log_done_at": None,
        "current_run_sessions": [],
        "skill_stats": {},
        "distilled_mapping": {},
    }


def _backup_legacy_synth_state(state_path: Path, raw: str) -> None:
    backup_path = state_path.with_name(state_path.name + ".pre_v2_backup")
    if backup_path.exists():
        return
    try:
        backup_path.write_text(raw, encoding="utf-8")
    except OSError as e:
        print(f"[synthesize] state backup failed (non-critical): {e}", file=sys.stderr)


def _migrate_synth_state_v1_to_v2(data: dict, state_path: Path,
                                  sessions_dir: Path | None) -> dict:
    uuid = data.get("last_synth_uuid")
    mtime = 0.0
    if uuid and sessions_dir is not None:
        session_path = find_session_by_uuid(sessions_dir, uuid)
        if session_path:
            try:
                mtime = session_path.stat().st_mtime
            except OSError:
                mtime = 0.0
    if not mtime and data.get("last_synth_at"):
        try:
            mtime = datetime.fromisoformat(data["last_synth_at"]).timestamp()
        except (ValueError, TypeError):
            mtime = 0.0

    migrated = _default_synth_state_v2()
    migrated.update({
        "sessions_since_last_synth": data.get("sessions_since_last_synth", 0),
        "last_synth_session_mtime": mtime,
        "last_synth_session_uuid": uuid,
        "skill_stats": data.get("skill_stats", {}),
        "distilled_mapping": data.get("distilled_mapping", {}),
    })
    safe_write(state_path, json.dumps(migrated, indent=2, ensure_ascii=False))
    return migrated


def _load_synth_state(state_path: Path, sessions_dir: Path | None = None) -> dict:
    raw = safe_read(state_path)
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _default_synth_state_v2()
        if "last_synth_session_mtime" not in data or "last_synth_at" in data:
            _backup_legacy_synth_state(state_path, raw)
            return _migrate_synth_state_v1_to_v2(data, state_path, sessions_dir)
        state = _default_synth_state_v2()
        state.update(data)
        return state
    return _default_synth_state_v2()


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
    if state.get("current_run_id") and state.get("current_run_sessions"):
        sessions = []
        for uuid in state.get("current_run_sessions", []):
            p = find_session_by_uuid(sessions_dir, uuid)
            if p:
                sessions.append(p)
        sessions.sort(key=lambda p: (p.stat().st_mtime, p.stem))
        return sessions
    return find_sessions_after(
        sessions_dir,
        after_mtime=float(state.get("last_synth_session_mtime", 0.0) or 0.0),
        after_uuid=state.get("last_synth_session_uuid"),
        excluded_uuids=None,
        limit=limit,
    )


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
- 對每個 pattern 評估 quality_score（0-3）：
  - 3：觸發條件具體（有明確信號詞）且包含至少一個可執行步驟（指令/程式碼/具體動作）
  - 2：可用，但觸發條件模糊或步驟過於抽象
  - 1：與現有 skill 清單中某條高度重疊，或內容只有原則性說明沒有任何可執行步驟
  - 0：完全冗餘或無意義
- quality_score ≤ 1 的 pattern 仍要輸出（供記錄），但不會被寫入系統

## 摩擦片段（Guard skill 原料）
{friction_text}

## 習慣片段（Workflow/Audit skill 原料）
{habit_text}

## 現有 skill 清單（比對重複或冗餘）
{existing_skills}

## 輸出格式（只輸出 JSON，不含任何解釋文字）
```json
{{
  "patterns": [
    {{
      "topic": "kebab-case-topic-name",
      "pattern_type": "guard",
      "evidence_sessions": 4,
      "root_cause": "execution-forget",
      "quality_score": 3,
      "quality_reason": "觸發條件具體，步驟包含可執行 bash 指令",
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


def _build_synthesis_prompt(friction: str, habit: str, existing_skills: str,
                            min_evidence: int = 3) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    friction_str = friction if friction else "（本次 sessions 無摩擦片段）"
    habit_str = habit if habit else "（本次 sessions 無習慣片段）"
    return SYNTHESIS_PROMPT.format(
        friction_text=friction_str,
        habit_text=habit_str,
        existing_skills=existing_skills,
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


def _validate_distill_output(data: dict) -> bool:
    """驗證 _call_distill_llm 的輸出：必須有 entries list。"""
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return False
    for entry in data["entries"]:
        if not isinstance(entry, dict):
            return False
        if not _is_safe_topic(entry.get("topic", "")):
            return False
        if not _has_required_frontmatter(
            entry.get("content", ""), ("name", "description", "type", "created")
        ):
            return False
        src_files = entry.get("source_files", [])
        if not isinstance(src_files, list):
            return False
        if any(not _is_safe_filename(src) for src in src_files):
            return False
    return True


def _call_distill_llm(prompt: str, cfg: dict, error_log: Path) -> list[dict] | None:
    """呼叫 LLM 執行蒸餾，回傳 entries list；失敗回傳 None 並記 error.log。"""
    raw = run_claude(prompt, cfg)
    if raw is None:
        append_log(error_log, "[synthesize] distill LLM call failed")
        return None
    parsed = _parse_synthesis_output(raw)
    if not parsed or not _validate_distill_output(parsed):
        append_log(error_log, f"[synthesize] distill JSON parse/validate failed. raw[:300]={str(raw)[:300]}")
        return None
    return parsed["entries"]


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
        if not _is_safe_topic(p.get("topic", "")):
            return False
        if p.get("pattern_type") not in ("guard", "workflow", "audit"):
            return False
        if not isinstance(p.get("skill_content"), str):
            return False
        if not _has_required_frontmatter(p["skill_content"], ("name", "description", "type")):
            return False
        try:
            quality_score = int(p.get("quality_score", 3))
        except (ValueError, TypeError):
            quality_score = 0
        if quality_score < 0 or quality_score > 3:
            return False
    for mem in data["memories"]:
        if not isinstance(mem, dict):
            return False
        if not _is_safe_filename(mem.get("filename", "")):
            return False
        if not _has_required_frontmatter(
            mem.get("content", ""), ("name", "description", "type", "created")
        ):
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
        if not safe_write(skill_path, content):
            raise OSError(f"safe_write failed: {skill_path}")
        print(f"[synthesize] skill written: {topic} (iteration {iteration})")
        return True
    except OSError as e:
        print(f"[synthesize] failed to write skill {topic}: {e}", file=sys.stderr)
        return False


# ── Memory 寫入 ───────────────────────────────────────────────────

def _write_memories(memories: list[dict], memory_dir: Path,
                    memory_index: Path, dry_run: bool) -> None:
    """將 memories 寫入 memory/ 或 memory/thoughts/，並更新 MEMORY.md 索引。

    路由規則：
      type: insight → memory/thoughts/<filename>
      其他類型    → memory/<filename>
    """
    if not memories:
        return

    for mem in memories:
        filename = mem.get("filename", "")
        content = mem.get("content", "").replace("\\n", "\n")
        if not filename or not content:
            continue

        # 從 frontmatter 讀 type 決定目標目錄
        type_match = re.search(r"^type:\s*(.+)$", content, re.MULTILINE)
        mem_type = type_match.group(1).strip() if type_match else "insight"

        if mem_type == "insight":
            target_dir = memory_dir / "thoughts"
            index_ref = f"thoughts/{filename}"
        else:
            target_dir = memory_dir
            index_ref = filename

        mem_path = target_dir / filename

        if dry_run:
            print(f"[dry-run] would write memory ({mem_type}): {mem_path}")
            continue

        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if not safe_write(mem_path, content):
                raise OSError(f"safe_write failed: {mem_path}")
            print(f"[synthesize] memory written ({mem_type}): {filename}")
        except OSError as e:
            print(f"[synthesize] failed to write memory {filename}: {e}", file=sys.stderr)
            continue

        # 更新 MEMORY.md 索引
        name_match = re.search(r"^name:\s*(.+)$", content, re.MULTILINE)
        desc_match = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
        if name_match and desc_match:
            name = name_match.group(1).strip()
            desc = desc_match.group(1).strip()
            index_line = f"- [{name}]({index_ref}) — {desc}\n"
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


# ── Knowledge Base 蒸餾 ───────────────────────────────────────────

DISTILL_PROMPT = """\
你是記憶蒸餾系統。將以下多條 {mem_type} 類型的原始記憶蒸餾為精煉知識條目。

## 任務規則
- 合併語義相近的條目（相似規則只保留最具體的版本）
- 比對「既有知識庫」，避免重複已有內容（有新細節才更新）
- 為每條輸出加 tags（3-5 個 kebab-case 關鍵字）
- 保留具體操作細節，不要過度概括
- 若所有原始記憶都已被知識庫涵蓋，entries 回傳空陣列

## 既有知識庫（已有，避免重複）
{existing_knowledge}

## 待蒸餾的原始記憶
{raw_memories}

## 輸出格式（只輸出 JSON，不含解釋文字）
```json
{{
  "entries": [
    {{
      "topic": "kebab-case-topic-name",
      "source_files": ["feedback_git_push_windows.md"],
      "tags": ["git", "windows", "credential"],
      "content": "---\\nname: 簡短標題\\ndescription: 一句話描述（用於索引）\\ntype: {mem_type}\\ntags: [git, windows, credential]\\ncreated: {today}\\nvalid_until: null\\nsuperseded_by: null\\n---\\n\\n具體內容..."
    }}
  ]
}}
```
"""


def _resolve_knowledge_dir(cfg: dict) -> Path:
    """知識庫路徑 = primary_project_dir/knowledge/"""
    return get_path(cfg, "primary_project_dir") / "knowledge"


def _collect_chunks_under_cap(files: list[Path], per_file_limit: int,
                              total_cap: int) -> list[str]:
    """讀檔組 chunks，總長度不超過 total_cap；單檔內容截斷至 per_file_limit。"""
    parts: list[str] = []
    total = 0
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")[:per_file_limit]
        chunk = f"### {f.name}\n{text}\n"
        if total + len(chunk) > total_cap:
            break
        parts.append(chunk)
        total += len(chunk)
    return parts


def _load_existing_knowledge(knowledge_dir: Path, mem_type: str,
                              ctx_cap: int) -> str:
    """讀取 knowledge/<type>/ 現有條目，供蒸餾時比對（總量限制 ctx_cap/2）。"""
    type_dir = knowledge_dir / mem_type
    if not type_dir.exists():
        return "（尚無既有知識）"
    files = sorted(type_dir.glob("*.md"))
    parts = _collect_chunks_under_cap(files, _EXISTING_FILE_READ_CHARS, ctx_cap // 2)
    return "\n".join(parts) if parts else "（尚無既有知識）"


def _distill_memories(memory_dir: Path, knowledge_dir: Path,
                      cfg: dict, state: dict, dry_run: bool,
                      error_log: Path) -> dict:
    """將 memory/ 原始條目蒸餾後寫入 knowledge/，回傳 distilled_mapping 更新。

    只處理 distill_min_entries 以上的類型。
    """
    min_entries = get_int(cfg, KB_KEY, "distill_min_entries", default=3)
    ctx_cap = get_int(cfg, KB_KEY, "ctx_cap_chars", default=8000)
    distilled_dir = memory_dir / "distilled"
    mapping: dict[str, str] = state.get("distilled_mapping", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 按 type 分組掃 memory/（排除 distilled/ 和 thoughts/）
    type_files: dict[str, list[Path]] = {kb_type: [] for kb_type in set(_TYPE_MAP.values())}
    for f in memory_dir.iterdir():
        if not f.is_file() or f.suffix != ".md":
            continue
        for prefix, kb_type in _TYPE_MAP.items():
            if f.name.startswith(prefix + "_"):
                type_files[kb_type].append(f)
                break

    for kb_type, files in type_files.items():
        if len(files) < min_entries:
            continue

        print(f"[synthesize] distilling {len(files)} {kb_type} memories...")

        raw_parts = _collect_chunks_under_cap(files, _RAW_FILE_READ_CHARS, ctx_cap // 2)
        raw_memories = "\n".join(raw_parts)
        existing = _load_existing_knowledge(knowledge_dir, kb_type, ctx_cap)

        prompt = DISTILL_PROMPT.format(
            mem_type=kb_type,
            existing_knowledge=existing,
            raw_memories=raw_memories,
            today=today,
        )

        if dry_run:
            print(f"[dry-run] would distill {kb_type}: {[f.name for f in files[:3]]}...")
            continue

        entries = _call_distill_llm(prompt, cfg, error_log)
        if entries is None:
            continue

        # 寫入 knowledge/ 並移走原始
        for entry in entries:
            topic = entry.get("topic", "").strip()
            content = entry.get("content", "").replace("\\n", "\n")
            src_files = entry.get("source_files", [])
            if not topic or not content:
                continue

            write_knowledge_entry(topic, content, knowledge_dir, kb_type)
            print(f"[synthesize] knowledge written: {kb_type}/{topic}.md")

            # 移走原始 memory 檔
            for src_name in src_files:
                src_path = memory_dir / src_name
                if src_path.exists():
                    move_to_distilled(src_path, distilled_dir)
                    mapping[src_name] = f"{kb_type}/{topic}.md"
                    print(f"[synthesize] distilled: {src_name}")

    return mapping


def _rebuild_knowledge_tags(knowledge_dir: Path, dry_run: bool) -> None:
    """重建 KNOWLEDGE_TAGS.md。"""
    tags_path = knowledge_dir / "KNOWLEDGE_TAGS.md"
    if dry_run:
        print(f"[dry-run] would rebuild KNOWLEDGE_TAGS.md in {knowledge_dir}")
        return
    update_knowledge_tags(knowledge_dir, tags_path)
    tag_count = sum(1 for line in tags_path.read_text(encoding="utf-8").splitlines()
                    if line.startswith("|") and "tag" not in line and "---" not in line)
    print(f"[synthesize] KNOWLEDGE_TAGS.md rebuilt: {tag_count} tag entries")


def _is_knowledge_base_enabled(cfg: dict) -> bool:
    """判斷是否啟用 knowledge base 蒸餾。

    新 schema：`knowledge_base.enabled: true`
    Legacy fallback：舊 config 用 `knowledge.enabled` 或推導自 `primary_project_dir` 存在
    （留著向後相容；新部署可直接刪 legacy 路徑）。
    """
    new_enabled = get_str(cfg, KB_KEY, "enabled", default="true").lower() != "false"

    if not isinstance(cfg, dict):
        return new_enabled

    legacy_cfg = cfg.get("knowledge", {})
    legacy_enabled = str(legacy_cfg.get("enabled", "")).lower() != "false"
    has_primary_project = bool(cfg.get("primary_project_dir"))

    return new_enabled and (legacy_enabled or has_primary_project)


def _prune_memory_index(memory_index: Path, mapping: dict,
                        max_lines: int, dry_run: bool) -> None:
    """移除 MEMORY.md 中已蒸餾進 knowledge/ 的條目，使行數 ≤ max_lines。"""
    if not memory_index.exists():
        return
    lines = memory_index.read_text(encoding="utf-8").splitlines(keepends=True)
    original_count = len(lines)

    # 找出已蒸餾的檔名集合
    distilled_names = set(mapping.keys())

    # 移除索引中引用了已蒸餾檔案的行
    kept = []
    for line in lines:
        # 檢查這行有沒有引用到已蒸餾的 memory 檔名
        referenced = any(name.replace(".md", "") in line for name in distilled_names)
        if not referenced:
            kept.append(line)

    # 若仍超過 max_lines，移除最舊的非 Thoughts 行
    non_thought_indices = [
        i for i, l in enumerate(kept)
        if l.strip().startswith("- [") and "thoughts/" not in l
    ]
    while len(kept) > max_lines and non_thought_indices:
        idx = non_thought_indices.pop(0)
        kept.pop(idx)
        non_thought_indices = [i - (1 if i > idx else 0) for i in non_thought_indices]

    if dry_run:
        print(f"[dry-run] MEMORY.md: {original_count} → {len(kept)} lines")
        return

    safe_write(memory_index, "".join(kept))
    print(f"[synthesize] MEMORY.md pruned: {original_count} → {len(kept)} lines")


# ── 主流程 ────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> int:
    cfg = load_config()
    print(f"[synthesize] primary_project_dir = {get_path(cfg, 'primary_project_dir')}")
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
    state = _load_synth_state(state_path, get_path(cfg, "sessions_dir"))

    # ── session 選取 / run 初始化 ─────────────────────────────────
    if state.get("current_run_id") is None:
        state["current_run_id"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["current_run_sessions"] = []
        state["current_run_summary"] = ""
        state["current_run_memories"] = []
        state["current_run_created_skills"] = []
        state["current_run_deleted_skills"] = []
        for stage in ("patterns_done_at", "memories_done_at", "distill_done_at",
                      "prune_done_at", "log_done_at"):
            state[stage] = None

    run_id = state["current_run_id"]
    sessions = _find_target_sessions(cfg, state)
    print(f"[synthesize] found {len(sessions)} sessions to analyze")
    if dry_run:
        for s in sessions:
            print(f"  - {s.stem[:16]}")

    if not sessions:
        print("[synthesize] no new sessions, skipping")
        state["current_run_id"] = None
        _save_synth_state(state_path, state, dry_run)
        return 0

    if not state.get("current_run_sessions"):
        state["current_run_sessions"] = [s.stem for s in sessions]
        _save_synth_state(state_path, state, dry_run)

    try:
        # ── patterns 階段：LLM call、skill 寫入、skill stats ───────
        if state.get("patterns_done_at") != run_id:
            friction_text, habit_text = _extract_all_fragments(sessions, cfg)
            total_chars = len(friction_text) + len(habit_text)
            print(f"[synthesize] fragments: friction={len(friction_text)}c, habit={len(habit_text)}c, total={total_chars}c")

            skill_usages = _scan_skill_usages(sessions)
            print(f"[synthesize] skill usages this cycle: {skill_usages}")

            min_evidence = get_int(cfg, SYNTH_STATE_KEY, "min_evidence_sessions", default=3)
            existing_skills = _load_existing_skill_descriptions(skills_dir)
            prompt = _build_synthesis_prompt(friction_text, habit_text, existing_skills, min_evidence)

            if dry_run:
                print("[dry-run] prompt preview (first 600 chars):")
                print(prompt[:600])
                print("...\n[dry-run] skipping LLM call")
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

            skill_stats = state.setdefault("skill_stats", {})
            created_skills: list[str] = []

            for pattern in patterns:
                topic = pattern.get("topic", "").strip()
                skill_content = pattern.get("skill_content", "").strip()
                if not topic or not skill_content:
                    continue

                try:
                    quality_score = int(pattern.get("quality_score", 3))
                except (ValueError, TypeError):
                    quality_score = 0
                quality_score = max(0, min(3, quality_score))
                quality_reason = pattern.get("quality_reason", "未說明")
                if quality_score < _QUALITY_SCORE_MIN:
                    print(f"[synthesize] skipped low-quality skill: {topic} (score={quality_score}, {quality_reason})")
                    continue

                existing_stat = skill_stats.get(topic, {})
                iteration = len(existing_stat.get("cycle_usages", [])) + 1

                if _write_skill(topic, skill_content, skills_dir, iteration, dry_run):
                    created_skills.append(topic)
                    if topic not in skill_stats:
                        skill_stats[topic] = {"cycle_usages": [], "low_count": 0, "status": "active"}

            deleted_skills = _update_skill_stats(skill_stats, skill_usages, cfg, skills_dir, dry_run)

            state["current_run_summary"] = summary
            state["current_run_memories"] = memories
            state["current_run_created_skills"] = created_skills
            state["current_run_deleted_skills"] = deleted_skills
            state["patterns_done_at"] = run_id
            _save_synth_state(state_path, state, dry_run)
        else:
            print("[synthesize] patterns phase already done, skipping")

        memory_lock_path = data_dir / "memory.lock"
        lock_ctx = nullcontext() if dry_run else FileLock(
            memory_lock_path, timeout=30, stale_timeout=600
        )
        with lock_ctx:
            if state.get("memories_done_at") != run_id:
                _write_memories(state.get("current_run_memories", []), memory_dir,
                                memory_index, dry_run)
                state["memories_done_at"] = run_id
                _save_synth_state(state_path, state, dry_run)
            else:
                print("[synthesize] memories phase already done, skipping")

            knowledge_enabled = _is_knowledge_base_enabled(cfg)
            max_lines = get_int(cfg, KB_KEY, "memory_hot_max_lines", default=50)

            if state.get("distill_done_at") != run_id:
                if knowledge_enabled:
                    knowledge_dir = _resolve_knowledge_dir(cfg)
                    new_mapping = _distill_memories(memory_dir, knowledge_dir, cfg, state, dry_run, error_log)
                    if new_mapping:
                        state.setdefault("distilled_mapping", {}).update(new_mapping)
                    _rebuild_knowledge_tags(knowledge_dir, dry_run)
                state["distill_done_at"] = run_id
                _save_synth_state(state_path, state, dry_run)
            else:
                print("[synthesize] distill phase already done, skipping")

            if state.get("prune_done_at") != run_id:
                if knowledge_enabled:
                    knowledge_dir = _resolve_knowledge_dir(cfg)
                    _prune_memory_index(memory_index, state.get("distilled_mapping", {}), max_lines, dry_run)
                state["prune_done_at"] = run_id
                _save_synth_state(state_path, state, dry_run)
            else:
                print("[synthesize] prune phase already done, skipping")

        if state.get("log_done_at") != run_id:
            _append_evolution_log(
                evolution_log_path,
                state.get("current_run_summary", ""),
                state.get("current_run_created_skills", []),
                state.get("current_run_deleted_skills", []),
                dry_run,
            )
            state["log_done_at"] = run_id
            _save_synth_state(state_path, state, dry_run)
        else:
            print("[synthesize] log phase already done, skipping")

    except TimeoutError:
        append_log(error_log, "[synthesize] memory.lock busy, aborting")
        print("[synthesize] memory.lock busy, aborting", file=sys.stderr)
        return 1
    except Exception as e:
        append_log(error_log, f"[synthesize] staged run failed: {e}")
        print(f"[synthesize] staged run failed: {e}", file=sys.stderr)
        return 1

    # ── synth_state 完成更新 ──────────────────────────────────────
    state["sessions_since_last_synth"] = 0
    if sessions:
        last_session = max(sessions, key=lambda p: (p.stat().st_mtime, p.stem))
        state["last_synth_session_mtime"] = last_session.stat().st_mtime
        state["last_synth_session_uuid"] = last_session.stem
    state["current_run_id"] = None
    state["current_run_summary"] = ""
    state["current_run_memories"] = []
    state["current_run_created_skills"] = []
    state["current_run_deleted_skills"] = []
    state["current_run_sessions"] = []
    for stage in ("patterns_done_at", "memories_done_at", "distill_done_at",
                  "prune_done_at", "log_done_at"):
        state[stage] = None
    _save_synth_state(state_path, state, dry_run)

    print("[synthesize] done")
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
