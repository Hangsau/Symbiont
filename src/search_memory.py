"""
search_memory.py — M10 語意記憶提取

功能：
  1. 接收自然語言 query
  2. 用 claude -p 將 query 展開為 concept list
  3. 掃 memory/ frontmatter 的 concepts 欄位（以及 knowledge/ 的 tags 欄位）
  4. 計算 overlap 分數，回傳 top-N 結果

用法：
  python src/search_memory.py "如何讓 agent 提取記憶"
  python src/search_memory.py "測試" --top-n 3 --min-score 0.1
  python src/search_memory.py "query" --memory-dir /path/to/memory

絕對禁忌：
  - exception 一律不往外拋；任何錯誤回傳空結果並印 stderr
  - 不寫入任何檔案
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config, get_path
from src.utils.claude_runner import run_claude


# ── 常數 ──────────────────────────────────────────────────────────

EXPAND_PROMPT = """\
將以下查詢展開為 3–8 個 kebab-case 語意概念標籤，用於搜尋記憶庫。

規則：
- 每個標籤為 kebab-case（全小寫，用 - 連接）
- 涵蓋同義詞、相關概念、上位概念
- 只輸出 JSON 陣列，不含任何解釋文字

查詢：{query}

輸出格式（只輸出 JSON）：
["concept-1", "concept-2", "concept-3"]
"""

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
TAGS_RE = re.compile(r"^(?:concepts|tags):\s*\[([^\]]*)\]", re.MULTILINE)


# ── 核心邏輯 ──────────────────────────────────────────────────────

def expand_query(query: str, cfg: dict) -> list[str]:
    """用 claude -p 將 query 展開為 concept list；失敗回傳空列表。"""
    if not query or not query.strip():
        return []
    prompt = EXPAND_PROMPT.format(query=query)
    result = run_claude(prompt, cfg)
    if not result:
        return []
    try:
        # 找 JSON 陣列（允許 LLM 輸出前後有文字）
        match = re.search(r"\[.*?\]", result, re.DOTALL)
        if not match:
            return []
        concepts = json.loads(match.group())
        if not isinstance(concepts, list):
            return []
        return [c.strip().lower() for c in concepts if isinstance(c, str) and c.strip()]
    except (json.JSONDecodeError, ValueError):
        return []


def _parse_md_metadata(path: Path) -> dict | None:
    """從 .md 文件一次讀取 concepts/tags、name、description。無 concepts 回傳 None。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    fm_match = FRONTMATTER_RE.match(text)
    if not fm_match:
        return None

    frontmatter = fm_match.group(1)
    tags_match = TAGS_RE.search(frontmatter)
    if not tags_match:
        return None

    raw = tags_match.group(1)
    concepts = [t.strip().strip('"\'').lower() for t in raw.split(",") if t.strip()]
    if not concepts:
        return None

    name_m = re.search(r"^name:\s*(.+)$", frontmatter, re.MULTILINE)
    desc_m = re.search(r"^description:\s*(.+)$", frontmatter, re.MULTILINE)
    return {
        "concepts": concepts,
        "name": name_m.group(1).strip() if name_m else None,
        "description": desc_m.group(1).strip() if desc_m else None,
    }


def _overlap_score(query_concepts: list[str], file_concepts: list[str]) -> float:
    """Jaccard-like overlap：共同概念數 / query 概念數（避免除以零）。"""
    if not query_concepts or not file_concepts:
        return 0.0
    q_set = set(query_concepts)
    f_set = set(file_concepts)
    return len(q_set & f_set) / len(q_set)


def search(
    query: str,
    memory_dir: Path,
    cfg: dict | None = None,
    top_n: int = 5,
    min_score: float = 0.2,
) -> list[dict]:
    """
    主搜尋函式。回傳 list of {path, score, name, description}，按 score 降序。
    失敗（找不到目錄、LLM 失敗等）回傳空列表。
    """
    if not memory_dir.exists():
        return []

    query_concepts = expand_query(query, cfg or {})
    if not query_concepts:
        print(f"[search_memory] warning: expand_query returned empty for '{query}'", file=sys.stderr)
        return []

    results: list[dict] = []

    # 掃 memory/ 和 knowledge/ 下所有 .md
    search_dirs = [memory_dir]
    knowledge_dir = get_path(cfg or {}, "primary_project_dir") / "knowledge"
    if knowledge_dir.exists():
        search_dirs.append(knowledge_dir)

    for base_dir in search_dirs:
        for md_path in base_dir.rglob("*.md"):
            if md_path.name in ("MEMORY.md", "KNOWLEDGE_TAGS.md"):
                continue
            if "_malformed" in md_path.parts:
                continue

            meta = _parse_md_metadata(md_path)
            if meta is None:
                continue

            score = _overlap_score(query_concepts, meta["concepts"])
            if score < min_score:
                continue

            results.append({
                "path": str(md_path),
                "score": round(score, 3),
                "name": meta["name"] or md_path.stem,
                "description": meta["description"] or "",
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n]


# ── CLI 入口 ──────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="M10 語意記憶搜尋")
    parser.add_argument("query", help="自然語言搜尋查詢")
    parser.add_argument("--top-n", type=int, default=None, help="回傳最多 N 條（覆蓋 config）")
    parser.add_argument("--min-score", type=float, default=None, help="最低分數（覆蓋 config）")
    parser.add_argument("--memory-dir", type=str, default=None, help="指定 memory 目錄（覆蓋 config）")
    parser.add_argument("--json", action="store_true", help="輸出 JSON 格式")
    args = parser.parse_args()

    try:
        cfg = load_config()
    except Exception as e:
        print(f"[search_memory] config load failed: {e}", file=sys.stderr)
        cfg = {}

    sm_cfg = cfg.get("search_memory", {})
    top_n = args.top_n if args.top_n is not None else sm_cfg.get("top_n", 5)
    min_score = args.min_score if args.min_score is not None else sm_cfg.get("min_score", 0.2)

    if args.memory_dir:
        memory_dir = Path(args.memory_dir)
    else:
        try:
            memory_dir = get_path(cfg, "memory_dir")
        except Exception:
            print("[search_memory] error: cannot resolve memory_dir", file=sys.stderr)
            return

    results = search(
        query=args.query,
        memory_dir=memory_dir,
        cfg=cfg,
        top_n=top_n,
        min_score=min_score,
    )

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        if not results:
            print("（無符合結果）")
            return
        for r in results:
            print(f"[{r['score']:.3f}] {r['name']}")
            print(f"  {r['path']}")
            if r["description"]:
                print(f"  {r['description']}")
            print()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[search_memory] unexpected error: {e}", file=sys.stderr)
        sys.exit(0)
