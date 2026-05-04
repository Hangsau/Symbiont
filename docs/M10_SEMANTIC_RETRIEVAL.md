# M10 — 語意記憶提取（Semantic Memory Retrieval）

> 狀態：規劃中（2026-05-02）
> 前置條件：M9（KNOWLEDGE_TAGS hook 注入）完成後實作 Phase 2

---

## 目標狀態

Symbiont 能依據當前任務語意，自動找出最相關的記憶並注入 session，不再靠 Grep 關鍵字或全量 MEMORY.md。

---

## 背景

現有檢索方式是 Grep 關鍵字 + 手動 Read，關鍵字不重疊的相關記憶無法被找到。
memory_audit 把 MEMORY.md 壓到 50 行是「縮小全量注入」策略，不是「按需語意取」。
MemOS / MemMachine 等競品用 graph + 語意搜尋解的正是這個問題。

---

## 三階段計畫

### Phase 1 — 寫入時加語意標籤（最低摩擦）

**改什麼：**
- `src/session_wrap.py`：LLM prompt + output schema 加 `concepts: [...]` 欄位；寫入 memory frontmatter
- `src/synthesize.py`：knowledge/ 生成時同樣輸出 `concepts`
- `concepts` 為 optional 欄位，schema 失敗 fallback 空列表，不影響現有寫入

**效果：**
每條新記憶和知識條目都帶有語意標籤，為 Phase 2 查詢做準備。

**成本：** 修改現有 LLM prompt，不加 API call，不加依賴。

---

### Phase 2 — 查詢腳本 + M9 整合

**新增：**
- `src/search_memory.py`：query → claude -p 展開 concepts → 掃 frontmatter → overlap 分數 → 回傳 top-N
- `scripts/run_search.py`：hook/CLI 入口，exception 一律 exit 0 不卡 session
- `config.yaml` 新增 `search_memory` 區塊

**config.yaml 預計新增：**
```yaml
search_memory:
  top_n: 5
  min_score: 0.2
  timeout_seconds: 30
```

**M9 整合：**
UserPromptSubmit hook 呼叫 `run_search.py`，注入 top-N 相關記憶（M9 milestone 時一起做，不在此 milestone）。

---

### Phase 3 — 輕量 embedding（選配）

視 Phase 2 效果（跑 2–4 週）決定是否實作：
- `src/utils/embedding_index.py`：本地 embedding 生成 + cosine similarity
- `data/memory_embeddings.json`：向量快取
- 依賴：`sentence-transformers`（~80MB）

---

## 已識別風險

| 風險 | 發生條件 | 預案 |
|------|---------|------|
| LLM concepts 標籤不一致 | 同主題生成不同標籤 | prompt 加 normalize 規則；寫入前 snake_case 標準化 |
| schema 驗證失敗 concepts 消失 | LLM 輸出非 list[str] | concepts 設 optional，失敗 fallback `[]` |
| Phase 2 hook 拖慢 session 啟動 | claude -p 超時 | timeout_seconds 設定；run_search.py 全程 try/except exit 0 |

---

## 驗收標準

**Phase 1：**
```bash
python src/session_wrap.py --dry-run
# memory candidate 含 concepts: [...] 欄位

python src/synthesize.py --dry-run
# knowledge 條目含 concepts: [...] 欄位
```

**Phase 2：**
```bash
python src/search_memory.py "如何讓 agent 提取記憶"
# 回傳 top-N 相關記憶路徑 + 分數

python src/search_memory.py "測試" --memory-dir /tmp/empty
# 空列表，exit 0，不 crash

python scripts/run_search.py "任意查詢"
# claude -p 失敗時仍 exit 0
```

---

## 不在此 milestone 的範圍

- M9 UserPromptSubmit hook 整合（M9 實作時一起做）
- Phase 3 embeddings（視 Phase 2 效果後獨立規劃）
- 現有 MEMORY.md 索引格式變更
- evolve.py / babysit.py / memory_audit.py
