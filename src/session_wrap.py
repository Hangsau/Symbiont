"""
session_wrap.py — Session 結束後自動補跑 /wrap 的 Step 0（Memory Audit）和 Step 2（Reflect）

功能：
  1. 從 pending_session_wrap.txt 取目標 session UUID（或 fallback 找最舊未處理 session）
  2. 解析 .jsonl session log
  3. 呼叫 claude -p 同時分析 memory candidates + insight（單次 LLM call）
  4. schema 驗證、confidence 過濾
  5. 寫入 memory/*.md、更新 MEMORY.md 索引
  6. 寫入 memory/thoughts/YYYY-MM-DD_*.md（如有 insight）
  7. schema 驗證失敗的條目 → 寫入 memory/_malformed/（安全網）
  8. 更新 cursor data/session_wrap_state.json

觸發方式：
  - Stop hook → 寫 pending_session_wrap.txt → scripts/run_evolve.py 或類似 wrapper poll
  - Task Scheduler → 開機補跑（pending_session_wrap.txt 存在時）
  - 手動：python src/session_wrap.py [--dry-run] [--session-uuid UUID]

絕對禁忌：
  - JSON 解析失敗時不寫任何 memory 檔案，只記 error.log
  - confidence < threshold 的 candidate 直接丟棄，不寫到任何地方
  - schema 驗證失敗的 candidate 只寫到 _malformed/，不污染主目錄
"""

import argparse
import json
import re
import sys
import time
import uuid as _uuid_mod
from contextlib import nullcontext
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config, get_path, get_int, get_str
from src.utils.session_reader import parse_session, find_session_by_uuid, find_sessions_after
from src.utils.claude_runner import run_claude, check_auth
from src.utils.file_ops import safe_read, safe_write, append_log, FileLock, rotate_log


# ── 常數 ──────────────────────────────────────────────────────────

MAX_TURN_CHARS = 800          # 單條對話截斷長度
MIN_TURNS = 3                 # session turns 少於此數 → 跳過
PROCESSED_RECENT_LIMIT = 50
WRAP_DONE_MAX_AGE_SECS = 900  # 15 分鐘
MEMORY_LOCK_TIMEOUT = 30      # FileLock timeout（秒）
MAX_VERSION_SUFFIX = 100      # 檔名衝突時 _v2..._v99 嘗試上限
ERROR_LOG_MAX_LINES = 2000    # rotate_log 觸發行數
DESCRIPTION_MAX_LEN = 200     # candidate description 長度上限
FILENAME_RE = re.compile(r"^[a-z0-9_]+\.md$")
SLUG_RE = re.compile(r"^[a-z0-9-]+$")
MALFORMED_DIR = "_malformed"


# ── Prompt ────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """\
你是 Claude Code 的記憶提取系統。分析以下 session，提取值得寫入 memory/ 的條目和認知洞見。

## 任務說明

**保守判斷**：不確定就回空。寧可漏掉一條，也不要寫入品質低的記憶。
**不重複現有記憶**：若候選語義上已有對應記憶檔案，填入 `existing_match`，不建立新條目。
**insight 必須有具體例子**：「加深了理解」這類空話不算洞見。三個 Q 都沒有具體答案 → `insight: null`。

## 現有 MEMORY.md（截斷至 {ctx_cap_chars} 字）

{memory_md_excerpt}

## Session 對話

{session_text}

## 輸出格式

只輸出 JSON，不含任何解釋文字。

```json
{{
  "memory_candidates": [
    {{
      "type": "feedback",
      "name": "短標題（人類可讀）",
      "description": "一行索引描述（≤150 字，用於判斷相關性）",
      "filename": "feedback_xxx.md（全小寫 kebab-case，依現有命名慣例，字元限 a-z 0-9 _）",
      "content": "完整檔案 body（純 markdown，不含 frontmatter，模組會自動加）",
      "concepts": ["kebab-case-concept-1", "kebab-case-concept-2"],
      "confidence": 0.0,
      "existing_match": null
    }}
  ],
  "insight": {{
    "title": "一句話標題",
    "description": "情境描述（用於 MEMORY.md ## Thoughts 索引行，≤150 字）",
    "domain": "system-design|teaching|gomoku|self-memory|general|...",
    "topic_slug": "kebab-case-topic（用於檔名 thoughts/YYYY-MM-DD_<slug>.md）",
    "understanding_change": "Q1：理解改變了什麼（有具體例子才填，無則空字串）",
    "surprise_decision": "Q2：意外的決策或結果（有具體例子才填，無則空字串）",
    "next_time": "Q3：下次先想到什麼（有具體例子才填，無則空字串）",
    "confidence": 0.0
  }}
}}
```

**type 只能是**：`feedback`、`project`、`reference`（三選一）。
**concepts**：2–5 個 kebab-case 語意標籤，供語意搜尋用；無法判斷填空陣列 `[]`。
**existing_match**：若疑似重複，填現有記憶的檔名（如 `feedback_xxx.md`）；確定不重複填 `null`。
**insight 三個 Q 全無實質答案** → 整個 `insight` 欄位改為 `null`（不是物件，是 JSON null）。
**memory_candidates 無候選** → 填空陣列 `[]`。
"""


# ── state.json 操作（仿 evolve.py）──────────────────────────────

def _default_state() -> dict:
    return {
        "last_processed_mtime": 0.0,
        "last_processed_uuid": None,
        "processed_recent": [],
        "processed_at": None,
    }


def _read_state(state_path: Path) -> dict:
    """讀取 session_wrap_state.json；損壞或不存在時回傳 default。"""
    raw = safe_read(state_path)
    if raw:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return _default_state()
        state = _default_state()
        state.update(data)
        if not isinstance(state.get("processed_recent"), list):
            state["processed_recent"] = []
        return state
    return _default_state()


def _write_state(state_path: Path, uuid: str, jsonl_path: Path, dry_run: bool) -> None:
    """更新 cursor。dry-run 時只印，不寫。"""
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
        print(f"[session_wrap] [dry-run] would write state: {state}")
    else:
        safe_write(state_path, json.dumps(state, indent=2, ensure_ascii=False))


# ── Session 選擇 ──────────────────────────────────────────────────

def _find_target_session(
    cfg: dict,
    explicit_uuid: str | None = None,
) -> tuple[Path | None, str | None]:
    """決定要處理的 session。回傳 (jsonl_path, uuid)。

    優先序：
      1. explicit_uuid（CLI --session-uuid）
      2. pending_session_wrap.txt 指定的 uuid
      3. fallback：cursor 之後最舊未處理 session
    """
    sessions_dir = get_path(cfg, "sessions_dir")
    root = Path(cfg["_root"])
    state_path = root / get_str(cfg, "paths", "session_wrap_state",
                                default="data/session_wrap_state.json")
    pending_path = root / get_str(cfg, "paths", "pending_session_wrap",
                                  default="data/pending_session_wrap.txt")

    # 1. explicit_uuid（debug 用，跳過 pending flag 邏輯）
    if explicit_uuid:
        p = find_session_by_uuid(sessions_dir, explicit_uuid)
        if p:
            print(f"[session_wrap] explicit uuid: {explicit_uuid}")
            return p, explicit_uuid
        print(f"[session_wrap] explicit uuid not found: {explicit_uuid}", file=sys.stderr)
        return None, None

    # 2. pending_session_wrap.txt
    if pending_path.exists():
        uuid = pending_path.read_text(encoding="utf-8").strip()
        if uuid:
            p = find_session_by_uuid(sessions_dir, uuid)
            if p:
                print(f"[session_wrap] pending session: {uuid}")
                return p, uuid
            print(f"[session_wrap] pending uuid not found: {uuid}", file=sys.stderr)
            pending_path.unlink(missing_ok=True)

    # 3. fallback：cursor 之後最舊
    state = _read_state(state_path)
    candidates = find_sessions_after(
        sessions_dir,
        after_mtime=float(state.get("last_processed_mtime", 0.0) or 0.0),
        after_uuid=state.get("last_processed_uuid"),
        excluded_uuids=set(state.get("processed_recent", [])),
        limit=1,
    )
    if not candidates:
        print("[session_wrap] 無新 session 需要處理")
        return None, None

    target = candidates[0]
    uuid = target.stem
    print(f"[session_wrap] new session: {uuid}")
    return target, uuid


# ── 格式化 turns ──────────────────────────────────────────────────

def _format_turns(turns: list[dict]) -> str:
    """格式化 session turns 為 prompt 用文字（仿 evolve.py）。"""
    parts = []
    for t in turns:
        role = "USER" if t["role"] == "user" else "ASSISTANT"
        content = t["content"][:MAX_TURN_CHARS]
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


# ── Prompt 組裝 ───────────────────────────────────────────────────

def _build_prompt(turns: list[dict], memory_md: str, ctx_cap_chars: int) -> str:
    """組裝 LLM prompt。"""
    if len(memory_md) > ctx_cap_chars:
        memory_excerpt = memory_md[:ctx_cap_chars] + "\n[...截斷...]"
    else:
        memory_excerpt = memory_md
    session_text = _format_turns(turns)
    return PROMPT_TEMPLATE.format(
        ctx_cap_chars=ctx_cap_chars,
        memory_md_excerpt=memory_excerpt,
        session_text=session_text,
    )


# ── JSON 解析（仿 evolve.py）────────────────────────────────────

def _extract_json(raw: str) -> dict | None:
    """從 LLM 輸出提取 JSON，容錯 markdown code fence。"""
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
                        return json.loads(raw[start : i + 1])
                    except json.JSONDecodeError:
                        break

    return None


# ── Schema 驗證 ───────────────────────────────────────────────────

def _sanitize_concepts(raw: object) -> list:
    """將 LLM 輸出的 concepts 欄位正規化為 list[str]；無效值 fallback 空列表。"""
    if not isinstance(raw, list):
        return []
    return [c.strip().lower() for c in raw if isinstance(c, str) and c.strip()]


def _validate_candidate(candidate: object) -> bool:
    """驗證 memory candidate 結構。"""
    if not isinstance(candidate, dict):
        return False
    for field in ("name", "description", "filename", "content"):
        val = candidate.get(field)
        if not isinstance(val, str) or not val.strip():
            return False
    if candidate.get("type") not in ("feedback", "project", "reference"):
        return False
    desc = candidate.get("description", "")
    if len(desc) > DESCRIPTION_MAX_LEN:
        return False
    conf = candidate.get("confidence")
    if not isinstance(conf, (int, float)):
        return False
    if not (0.0 <= float(conf) <= 1.0):
        return False
    fname = candidate.get("filename", "")
    if not FILENAME_RE.match(fname):
        return False
    return True


def _validate_insight(insight: object) -> bool:
    """驗證 insight 結構。"""
    if not isinstance(insight, dict):
        return False
    for field in ("title", "description", "domain", "topic_slug"):
        val = insight.get(field)
        if not isinstance(val, str) or not val.strip():
            return False
    if not SLUG_RE.match(insight.get("topic_slug", "")):
        return False
    # 三個 Q 欄位至少一個非空
    q_values = [
        insight.get("understanding_change", ""),
        insight.get("surprise_decision", ""),
        insight.get("next_time", ""),
    ]
    if not any(isinstance(q, str) and q.strip() for q in q_values):
        return False
    conf = insight.get("confidence")
    if not isinstance(conf, (int, float)):
        return False
    if not (0.0 <= float(conf) <= 1.0):
        return False
    return True


# ── Frontmatter 產生 ──────────────────────────────────────────────

def _make_frontmatter(candidate: dict, today_str: str) -> str:
    """依 SCHEMA.md 規格產生 frontmatter。"""
    memory_type = candidate["type"]
    # project / reference → review_by 今天 + 90 天；feedback → null
    if memory_type in ("project", "reference"):
        review_by = (date.today() + timedelta(days=90)).isoformat()
    else:
        review_by = "null"

    concepts = candidate.get("concepts") or []
    concepts_line = f"concepts: [{', '.join(concepts)}]\n"
    return (
        f"---\n"
        f"name: {candidate['name']}\n"
        f"description: {candidate['description']}\n"
        f"type: {memory_type}\n"
        f"created: {today_str}\n"
        f"valid_until: null\n"
        f"review_by: {review_by}\n"
        f"superseded_by: null\n"
        f"{concepts_line}"
        f"---\n\n"
    )


def _make_insight_frontmatter(insight: dict, today_str: str) -> str:
    """產生 insight 檔案的 frontmatter。"""
    return (
        f"---\n"
        f"name: {insight['title']}\n"
        f"description: {insight['description']}\n"
        f"type: insight\n"
        f"domain: {insight['domain']}\n"
        f"date: {today_str}\n"
        f"---\n\n"
    )


# ── 寫 malformed ──────────────────────────────────────────────────

def _write_malformed(
    memory_dir: Path,
    filename_hint: str,
    raw_content: str,
    audit_log: Path,
    dry_run: bool,
) -> None:
    """將無效 LLM 輸出寫入 _malformed/，不污染主目錄。"""
    malformed_dir = memory_dir / MALFORMED_DIR
    if not dry_run:
        malformed_dir.mkdir(exist_ok=True)

    safe_name = filename_hint if FILENAME_RE.match(filename_hint) else f"malformed_{_uuid_mod.uuid4().hex[:8]}.md"
    dest = malformed_dir / safe_name

    content = f"# MALFORMED LLM OUTPUT\n\n{raw_content}\n"
    if dry_run:
        print(f"[session_wrap] [dry-run] would write malformed: {dest}")
    else:
        safe_write(dest, content)
        append_log(audit_log, f"[session_wrap] malformed candidate written: {dest.name}")


# ── MEMORY.md 索引更新 ────────────────────────────────────────────

def _append_memory_index_line(index_path: Path, name: str, filename: str,
                               description: str) -> bool:
    """在 MEMORY.md 加入一條索引行。

    位置：最後一條 `-` 行之後、## Thoughts 之前；
          找不到 ## Thoughts 就 append 到檔末。
    回傳是否成功。
    """
    content = safe_read(index_path) or ""
    new_line = f"- [{name}]({filename}) — {description}\n"

    # 找 ## Thoughts section 起始位置
    thoughts_idx = content.find("\n## Thoughts")
    if thoughts_idx == -1:
        thoughts_idx = content.find("## Thoughts")
        if thoughts_idx == 0:
            pass  # 在最開頭（罕見）
        else:
            thoughts_idx = -1

    if thoughts_idx != -1:
        # 在 ## Thoughts 前插入（找到 ## Thoughts 前最後一個 `-` 行後）
        before = content[:thoughts_idx]
        after = content[thoughts_idx:]
        # 在 before 末尾找最後一條 - 行的位置
        last_bullet = before.rfind("\n- ")
        if last_bullet != -1:
            # 在最後一條 bullet 行的行末插入
            line_end = before.find("\n", last_bullet + 1)
            if line_end == -1:
                line_end = len(before)
            updated = before[:line_end + 1] + new_line + before[line_end + 1:] + after
        else:
            updated = before.rstrip("\n") + "\n" + new_line + after
    else:
        # 無 ## Thoughts → 直接 append
        updated = content.rstrip("\n") + "\n" + new_line

    return safe_write(index_path, updated)


def _append_thoughts_index_line(index_path: Path, title: str,
                                  rel_path: str, description: str) -> bool:
    """在 MEMORY.md 的 ## Thoughts section 末尾加入 insight 索引行。

    若 ## Thoughts section 不存在，在檔末新建。
    """
    content = safe_read(index_path) or ""
    new_line = f"- [{title}]({rel_path}) — {description}\n"

    thoughts_header = "## Thoughts"
    idx = content.find(thoughts_header)
    if idx == -1:
        # 新建 ## Thoughts section
        updated = content.rstrip("\n") + f"\n\n{thoughts_header}\n{new_line}"
    else:
        # 找 ## Thoughts section 結尾（下一個 ## 或檔末）
        next_section = content.find("\n## ", idx + len(thoughts_header))
        section_end = next_section if next_section != -1 else len(content)
        section_content = content[idx:section_end]

        # 找 section 內最後一條 - 行
        last_bullet = section_content.rfind("\n- ")
        if last_bullet != -1:
            abs_pos = idx + last_bullet
            line_end = content.find("\n", abs_pos + 1)
            if line_end == -1:
                line_end = len(content)
            updated = content[:line_end + 1] + new_line + content[line_end + 1:]
        else:
            # section 內沒有任何 bullet → 緊接在 header 後
            header_end = idx + len(thoughts_header)
            nl = content.find("\n", header_end)
            insert_at = (nl + 1) if nl != -1 else len(content)
            updated = content[:insert_at] + new_line + content[insert_at:]

    return safe_write(index_path, updated)


# ── 寫 memory 檔案 ────────────────────────────────────────────────

def _resolve_filename(memory_dir: Path, filename: str) -> Path:
    """解析最終寫入路徑，衝突時 append _v2 / _v3。"""
    dest = memory_dir / filename
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    for i in range(2, MAX_VERSION_SUFFIX):
        candidate = memory_dir / f"{stem}_v{i}{suffix}"
        if not candidate.exists():
            return candidate
    # 極罕見：回傳帶時間戳的名稱
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    return memory_dir / f"{stem}_{ts}{suffix}"


def _write_memory_candidate(
    candidate: dict,
    memory_dir: Path,
    index_path: Path,
    today_str: str,
    dry_run: bool,
    audit_log: Path,
) -> bool:
    """寫入一個 memory candidate，並更新 MEMORY.md 索引。

    rollback 策略：先寫檔案，再更新索引。
    若更新索引失敗，檔案已存在但索引未更新 → 記 error log，不刪檔（孤兒檔案優於 broken link）。
    回傳是否成功寫入。
    """
    dest = _resolve_filename(memory_dir, candidate["filename"])
    frontmatter = _make_frontmatter(candidate, today_str)
    full_content = frontmatter + candidate["content"].strip() + "\n"

    if dry_run:
        print(f"[session_wrap] [dry-run] would write: {dest.name}")
        print(f"  name: {candidate['name']}")
        print(f"  type: {candidate['type']}, confidence: {candidate['confidence']:.2f}")
        return True

    if not safe_write(dest, full_content):
        append_log(audit_log,
                   f"[session_wrap] failed to write memory file: {dest.name}")
        return False

    # 更新 MEMORY.md 索引
    ok = _append_memory_index_line(
        index_path,
        name=candidate["name"],
        filename=dest.name,
        description=candidate["description"],
    )
    if not ok:
        append_log(audit_log,
                   f"[session_wrap] written {dest.name} but failed to update MEMORY.md index")
        # 不刪已寫的檔案（孤兒檔案優於 broken link）

    print(f"[session_wrap] memory written: {dest.name}")
    return True


def _write_insight(
    insight: dict,
    memory_dir: Path,
    index_path: Path,
    today_str: str,
    dry_run: bool,
    audit_log: Path,
) -> bool:
    """寫入 insight 到 thoughts/，並更新 MEMORY.md ## Thoughts 索引。"""
    thoughts_dir = memory_dir / "thoughts"
    slug = insight["topic_slug"]
    filename = f"{today_str}_{slug}.md"
    dest = thoughts_dir / filename

    # 衝突處理
    if dest.exists() and not dry_run:
        ts = datetime.now().strftime("%H%M%S")
        filename = f"{today_str}_{slug}_{ts}.md"
        dest = thoughts_dir / filename

    frontmatter = _make_insight_frontmatter(insight, today_str)
    body_parts = []

    understanding = insight.get("understanding_change", "").strip()
    surprise = insight.get("surprise_decision", "").strip()
    next_time = insight.get("next_time", "").strip()

    body_parts.append(f"## 理解改變\n{understanding if understanding else '（無）'}")
    if surprise:
        body_parts.append(f"## 意外決策（如有）\n{surprise}")
    body_parts.append(f"## 下次先想到\n{next_time if next_time else '（無）'}")

    full_content = frontmatter + "\n\n".join(body_parts) + "\n"
    rel_path = f"thoughts/{filename}"

    if dry_run:
        print(f"[session_wrap] [dry-run] would write insight: {rel_path}")
        print(f"  title: {insight['title']}")
        print(f"  domain: {insight['domain']}, confidence: {insight['confidence']:.2f}")
        return True

    thoughts_dir.mkdir(exist_ok=True)
    if not safe_write(dest, full_content):
        append_log(audit_log,
                   f"[session_wrap] failed to write insight: {filename}")
        return False

    ok = _append_thoughts_index_line(
        index_path,
        title=insight["title"],
        rel_path=rel_path,
        description=insight["description"],
    )
    if not ok:
        append_log(audit_log,
                   f"[session_wrap] written {filename} but failed to update MEMORY.md ## Thoughts")

    print(f"[session_wrap] insight written: {rel_path}")
    return True


# ── helpers ──────────────────────────────────────────────────────

def _clear_pending_if_safe(pending_path: Path, dry_run: bool,
                            explicit_uuid: str | None) -> None:
    """正常路徑結束時清 pending flag。
    dry_run / explicit_uuid 模式下保留（避免影響真實狀態）。"""
    if not dry_run and pending_path.exists() and explicit_uuid is None:
        pending_path.unlink(missing_ok=True)


def _format_confidence(conf: object) -> str:
    """容錯 confidence 格式化（非數字回 repr，避免 :.2f TypeError）。"""
    if isinstance(conf, (int, float)):
        return f"{float(conf):.2f}"
    return repr(conf)


# ── 主流程 ────────────────────────────────────────────────────────

def run(
    dry_run: bool = False,
    skip_if_wrap_done: bool = False,
    explicit_uuid: str | None = None,
) -> int:
    """主流程。回傳 exit code（0=成功，1=失敗）。"""
    cfg = load_config()
    root = Path(cfg["_root"])
    error_log = get_path(cfg, "error_log")
    audit_log = get_path(cfg, "audit_log")
    rotate_log(error_log, max_lines=ERROR_LOG_MAX_LINES)

    # 路徑解析
    state_path = root / get_str(cfg, "paths", "session_wrap_state",
                                default="data/session_wrap_state.json")
    pending_path = root / get_str(cfg, "paths", "pending_session_wrap",
                                  default="data/pending_session_wrap.txt")
    wrap_done_path = get_path(cfg, "wrap_done_file")

    # session_wrap 設定（找不到欄位都用 default，不 raise）
    sw_cfg = cfg.get("session_wrap", {})
    enabled = sw_cfg.get("enabled", True)
    auto_write = sw_cfg.get("auto_write", True)
    confidence_threshold = float(sw_cfg.get("confidence_threshold", 0.8))
    ctx_cap_chars = int(sw_cfg.get("ctx_cap_chars", 8000))
    skip_if_wrap_done_cfg = sw_cfg.get("skip_if_wrap_done", True)

    # --dry-run 覆寫 auto_write
    if dry_run:
        auto_write = False

    if not enabled:
        print("[session_wrap] enabled: false → 跳過")
        return 0

    # ── auth 檢查 ─────────────────────────────────────────────────
    if not check_auth():
        msg = "auth check failed: ~/.claude/.credentials.json not found"
        append_log(error_log, f"[session_wrap] {msg}")
        print(f"[session_wrap] {msg}", file=sys.stderr)
        return 1

    # ── wrap_done.txt 互斥檢查 ────────────────────────────────────
    effective_skip = skip_if_wrap_done or skip_if_wrap_done_cfg
    if effective_skip and explicit_uuid is None:
        if wrap_done_path.exists():
            try:
                age = time.time() - wrap_done_path.stat().st_mtime
            except OSError:
                age = WRAP_DONE_MAX_AGE_SECS + 1

            if age < WRAP_DONE_MAX_AGE_SECS:
                print(f"[session_wrap] skip: wrap done within {age:.0f}s")
                # 外層 if 已限定 explicit_uuid is None
                _clear_pending_if_safe(pending_path, dry_run, explicit_uuid)
                return 0

    # ── 找目標 session ────────────────────────────────────────────
    jsonl_path, uuid = _find_target_session(cfg, explicit_uuid=explicit_uuid)
    if jsonl_path is None:
        return 0

    # ── 解析 session ──────────────────────────────────────────────
    max_turns = get_int(cfg, "session_reader", "max_turns", default=50)
    turns = parse_session(jsonl_path, max_turns=max_turns)

    if len(turns) < MIN_TURNS:
        print(f"[session_wrap] skip: session too short ({len(turns)} turns < {MIN_TURNS})")
        _write_state(state_path, uuid, jsonl_path, dry_run)
        _clear_pending_if_safe(pending_path, dry_run, explicit_uuid)
        return 0

    print(f"[session_wrap] parsed {len(turns)} turns from {uuid}")

    # ── 讀 MEMORY.md ──────────────────────────────────────────────
    try:
        memory_dir = get_path(cfg, "memory_dir")
        index_path = get_path(cfg, "memory_index")
    except RuntimeError as e:
        append_log(error_log, f"[session_wrap] 路徑解析失敗：{e}")
        print(f"[session_wrap] 路徑解析失敗：{e}", file=sys.stderr)
        return 1

    memory_md = safe_read(index_path) or ""

    # ── 組 prompt → claude -p ─────────────────────────────────────
    prompt = _build_prompt(turns, memory_md, ctx_cap_chars)
    print("[session_wrap] calling claude -p ...")

    if dry_run:
        print("[session_wrap] [dry-run] prompt preview (first 500 chars):")
        print(prompt[:500])
        print("...\n[session_wrap] [dry-run] skipping actual LLM call")
        parsed = {"memory_candidates": [], "insight": None}
    else:
        raw_output = run_claude(prompt, cfg)
        if raw_output is None:
            # run_claude 已記 error.log；不推進 cursor，pending 留著
            return 1

        parsed = _extract_json(raw_output)
        if parsed is None:
            ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
            msg = (f"[{ts}] JSON parse failed for session {uuid}. "
                   f"raw output:\n{raw_output[:500]}")
            append_log(error_log, f"[session_wrap] {msg}")
            print("[session_wrap] JSON parse failed → error.log only", file=sys.stderr)
            return 1  # 不推進 cursor

    candidates_raw = parsed.get("memory_candidates", [])
    insight_raw = parsed.get("insight")  # 可能是 None 或 dict

    if not isinstance(candidates_raw, list):
        candidates_raw = []

    print(f"[session_wrap] candidates: {len(candidates_raw)}, insight: {'yes' if insight_raw else 'no'}")

    # ── 無任何輸出 → 推進 cursor 正常退出 ────────────────────────
    if not candidates_raw and not insight_raw:
        print("[session_wrap] 無 memory candidates 也無 insight，推進 cursor")
        _write_state(state_path, uuid, jsonl_path, dry_run)
        _clear_pending_if_safe(pending_path, dry_run, explicit_uuid)
        return 0

    # ── 取得 memory.lock（dry-run 跳過）─────────────────────────
    memory_lock_path = root / "data" / "memory.lock"
    lock = nullcontext() if dry_run else FileLock(memory_lock_path,
                                                   timeout=MEMORY_LOCK_TIMEOUT)

    today_str = date.today().isoformat()
    written_count = 0
    malformed_count = 0
    discarded_count = 0

    try:
        with lock:
            written_count, malformed_count, discarded_count = _process_outputs(
                candidates_raw=candidates_raw,
                insight_raw=insight_raw,
                memory_dir=memory_dir,
                index_path=index_path,
                today_str=today_str,
                confidence_threshold=confidence_threshold,
                auto_write=auto_write,
                dry_run=dry_run,
                audit_log=audit_log,
            )
    except TimeoutError:
        msg = "[session_wrap] memory.lock busy → skipping (pending 留著，下次重試)"
        append_log(audit_log, msg)
        print(msg)
        return 1

    # ── 輸出摘要 ──────────────────────────────────────────────────
    print(f"[session_wrap] done: written={written_count}, "
          f"malformed={malformed_count}, discarded={discarded_count}")

    # ── 更新 cursor + 清 pending ──────────────────────────────────
    _write_state(state_path, uuid, jsonl_path, dry_run)
    _clear_pending_if_safe(pending_path, dry_run, explicit_uuid)

    return 0


# ── 處理 candidates + insight 的子流程（從 run() 拆出，降低 God Function 程度）─

def _process_outputs(
    *,
    candidates_raw: list,
    insight_raw: object,
    memory_dir: Path,
    index_path: Path,
    today_str: str,
    confidence_threshold: float,
    auto_write: bool,
    dry_run: bool,
    audit_log: Path,
) -> tuple[int, int, int]:
    """處理所有 candidates 和 insight。回傳 (written, malformed, discarded)。
    呼叫前必須持有 memory.lock。"""
    written_count = 0
    malformed_count = 0
    discarded_count = 0

    # ── 處理 memory_candidates ────────────────────────────────
    for candidate in candidates_raw:
        if not isinstance(candidate, dict):
            continue

        # confidence < threshold → 直接丟棄，不寫任何地方
        conf = candidate.get("confidence", 0.0)
        if not isinstance(conf, (int, float)) or float(conf) < confidence_threshold:
            discarded_count += 1
            print(f"[session_wrap] discard (confidence={_format_confidence(conf)} "
                  f"< {confidence_threshold}): {candidate.get('name', '?')}")
            continue

        # schema 驗證失敗 → _malformed/
        if not _validate_candidate(candidate):
            malformed_count += 1
            raw_repr = json.dumps(candidate, ensure_ascii=False, indent=2)
            _write_malformed(
                memory_dir,
                filename_hint=str(candidate.get("filename", "malformed.md")),
                raw_content=raw_repr,
                audit_log=audit_log,
                dry_run=dry_run,
            )
            continue

        # concepts sanitize（optional 欄位，LLM 輸出異常時 fallback []）
        candidate["concepts"] = _sanitize_concepts(candidate.get("concepts"))

        # existing_match → 跳過（不建立重複條目）
        existing_match = candidate.get("existing_match")
        if existing_match and isinstance(existing_match, str) and existing_match.strip():
            print(f"[session_wrap] skip (existing_match={existing_match}): {candidate['name']}")
            continue

        if auto_write or dry_run:
            ok = _write_memory_candidate(
                candidate, memory_dir, index_path, today_str, dry_run, audit_log
            )
            if ok:
                written_count += 1

    # ── 處理 insight ──────────────────────────────────────────
    if insight_raw and isinstance(insight_raw, dict):
        conf = insight_raw.get("confidence", 0.0)
        if not isinstance(conf, (int, float)) or float(conf) < confidence_threshold:
            print(f"[session_wrap] discard insight "
                  f"(confidence={_format_confidence(conf)} < {confidence_threshold})")
        elif not _validate_insight(insight_raw):
            malformed_count += 1
            raw_repr = json.dumps(insight_raw, ensure_ascii=False, indent=2)
            slug = insight_raw.get("topic_slug", "insight")
            _write_malformed(
                memory_dir,
                filename_hint=f"insight_{slug}.md",
                raw_content=raw_repr,
                audit_log=audit_log,
                dry_run=dry_run,
            )
        else:
            if auto_write or dry_run:
                _write_insight(insight_raw, memory_dir, index_path,
                               today_str, dry_run, audit_log)

    return written_count, malformed_count, discarded_count


# ── CLI 入口 ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="session_wrap.py — 自動補跑 /wrap Step 0（Memory Audit）+ Step 2（Reflect）"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="分析並印出結果，不寫入任何檔案")
    parser.add_argument("--skip-if-wrap-done", action="store_true",
                        help="wrap_done.txt 在 15 分鐘內存在則靜默退出")
    parser.add_argument("--session-uuid", metavar="UUID",
                        help="手動指定 session uuid（debug 用）")
    args = parser.parse_args()

    sys.exit(run(
        dry_run=args.dry_run,
        skip_if_wrap_done=args.skip_if_wrap_done,
        explicit_uuid=args.session_uuid,
    ))


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
