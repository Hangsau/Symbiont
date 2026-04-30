# Memory Lock Protocol

來源：`docs/CODE_REVIEW_FINDINGS.md` Finding #6（memory_audit 與 synthesize 同時改 MEMORY.md 無共用 lock）。
任務：`docs/IMPROVEMENT_PLAN.md` M1.1-B。
產出時間：2026-04-30
狀態：設計文件，**尚未實作**。實作交給 M2.1-B（synthesize 階段化時一起改）並補一條獨立 task 給 memory_audit 端。

---

## 1. 動機

`synthesize` 與 `memory_audit` 都會修改：
- `MEMORY.md`（同時 append + prune + remove，三方互相覆蓋）
- `memory/<filename>.md`（synthesize move 到 distilled/、memory_audit move 到 archive/）
- `memory/thoughts/<filename>.md`（synthesize 寫入、memory_audit 歸檔）

兩者排程時間不同（audit 登入觸發、synthesize 由 evolve 計數器觸發），但都可能在背景被 Task Scheduler / Stop hook 拉起。同一個 MEMORY.md append 與 prune 同時跑，後者會直接覆蓋前者。

`memory_audit._remove_from_memory_index` 已有 `FileLock(memory_index.lock)`，但其他寫入點（synthesize 全部、memory_audit 的 archive 操作本身）都沒用同一把鎖，等於只擋了一個入口。

## 2. 寫入點盤點

下表列出所有改動 `memory/`、`MEMORY.md`、`knowledge/` 的點。`skills_dir`（`~/.claude/skills/`）與 `~/.claude/` 整體 backup 不在此範圍內。

| # | 檔案 | 函式 | 行號 | 操作 | 目前是否持鎖 |
|---|------|------|------|------|-------------|
| 1 | `memory_audit.py` | `_remove_from_memory_index` | 82-100 | 重寫 MEMORY.md | ✅ `memory_index.lock` |
| 2 | `memory_audit.py` | `_archive_file` | 105-138 | 寫 `archive/<f>`、刪 `memory/<f>`、呼叫 #1 | ❌ |
| 3 | `memory_audit.py` | `_archive_oldest_thoughts` | 143-187 | 刪 `memory/thoughts/<f>`、append `archive/thoughts-index.md` | ❌ |
| 4 | `synthesize.py` | `_write_skill` | 306-332 | 寫 `skills_dir/<topic>/SKILL.md` | ❌（不在本 protocol 範圍） |
| 5 | `synthesize.py` | `_write_memories` | 337-392 | 寫 `memory/<f>` 或 `memory/thoughts/<f>`、**直接 append `MEMORY.md`**（line 388-390） | ❌ |
| 6 | `synthesize.py` | `_distill_memories` | 548-626 | 呼叫 #8、#10 | ❌ |
| 7 | `synthesize.py` | `_run_update_knowledge_tags` | 629-638 | 重建 `knowledge/KNOWLEDGE_TAGS.md` | ❌ |
| 8 | `synthesize.py` | `_prune_memory_index` | 641-675 | 重寫 `MEMORY.md` | ❌ |
| 9 | `knowledge_writer.py` | `write_knowledge_entry` | 17-34 | 寫 `knowledge/<type>/<topic>.md` | ❌ |
| 10 | `knowledge_writer.py` | `update_knowledge_tags` | 37-99 | 寫 `KNOWLEDGE_TAGS.md` | ❌ |
| 11 | `knowledge_writer.py` | `move_to_distilled` | 147-161 | rename `memory/<f>` 到 `memory/distilled/<f>` | ❌ |

不在範圍內：
- `evolve.py` 不寫 `memory/`，只 `_run_backup`（robocopy/rsync）會讀整個 `~/.claude/`。read-only mirror，本 protocol 不規範。
- `synthesize._write_skill` 寫 `~/.claude/skills/`，與 memory 系統無資料相依，由另外的 lock 管（目前無）。

## 3. 鎖定義

```
路徑       data/memory.lock
類別       file_ops.FileLock（O_CREAT | O_EXCL）
timeout    取鎖等待 30 秒
stale      600 秒（10 分鐘）—— 比所有實際操作都長
```

**取代** `memory_audit._remove_from_memory_index` 內現有的 `memory_index.lock`。實作時直接改路徑、刪 `memory_index.lock` 任何殘留檔。

## 4. 持鎖規則

### 必須持有 `memory.lock`

呼叫端**進入函式前先取鎖**，rather than 在函式內取（避免巢狀 acquire 同把 lock）：

| 入口函式 | 持鎖範圍 |
|---------|---------|
| `memory_audit.run()` | 整個 `run()`（從 dry_run 檢查後到結束） |
| `synthesize.run()` 的 memories 階段 | `_write_memories` 全程 |
| `synthesize.run()` 的 distill 階段 | `_distill_memories` + `_run_update_knowledge_tags` + `_prune_memory_index`（一個 with 區塊） |

子函式（`_archive_file`、`_remove_from_memory_index`、`write_knowledge_entry`、`move_to_distilled` 等）**不再自己取鎖**——假設呼叫者已持有。

### 不需要持鎖

- `_load_synth_state` / `_save_synth_state`（只讀寫 `data/synth_state.json`，由 `synth_state.lock` 管）
- `_extract_all_fragments` / `_scan_skill_usages`（純讀 sessions JSONL）
- `_load_existing_skill_descriptions` / `_load_existing_knowledge`（純讀）
- `_write_skill`（寫 skills 目錄，不在 memory 範圍）
- `_append_evolution_log`（append `evolution_log.md`，是 append-only log，無覆蓋風險）
- `evolve.py` 全部（不寫 memory/，CLAUDE.md 由獨立 `CLAUDE.md.lock` 管）

## 5. 鎖取得順序

避免 deadlock 的全域順序：

```
synth_state.lock
    └─> memory.lock
            └─> CLAUDE.md.lock  (僅 evolve 用，不會與 memory.lock 嵌套)
```

具體規則：
- 持有 `memory.lock` 時，**不可**再取 `synth_state.lock`、`babysit.lock`、`CLAUDE.md.lock`。
- `babysit.lock` 與 `memory.lock` 不會互相嵌套（babysit 不寫 memory）。
- `evolve._increment_synth_counter` 會持 `synth_state.lock` 並 `subprocess.Popen` 啟動 synthesize 子進程——子進程是獨立進程、會自行重新申請 lock，不算嵌套。

## 6. 失敗行為

| 情境 | 行為 |
|------|------|
| `memory_audit.run()` 取不到鎖（30 秒內） | log 一條 `[memory_audit] memory.lock busy, skipping` 並 `return 0`，不視為錯誤 |
| `synthesize.run()` memories 階段取不到鎖 | log + raise，整個 synthesize run return 1（待 M2.1-B 階段化後可細化）|
| stale lock（mtime > 600 秒） | `FileLock` 自動視為廢棄、強制重取（既有行為） |

理由：`memory_audit` 是補性運維，跳過一輪沒關係；`synthesize` 是有狀態的進化流程，被擋住時應該明確失敗而非偷偷跳過。

## 7. 實作清單（給 M2.1-B 與後續 task）

### 對 synthesize.py（M2.1-B 處理）

```python
# run() 結構（簡化）
with FileLock(memory_lock_path, timeout=30, stale_timeout=600):
    if not state["memories_done_at"]:
        _write_memories(...)
        state["memories_done_at"] = run_id
        _save_synth_state(...)

    if not state["distill_done_at"]:
        _distill_memories(...)
        _run_update_knowledge_tags(...)
        _prune_memory_index(...)
        state["distill_done_at"] = run_id
        _save_synth_state(...)
```

注意：`_save_synth_state` 在 `memory.lock` 持有期間呼叫，違反「持有 memory.lock 時不取 synth_state.lock」？**by design**：`_save_synth_state` 不取 `synth_state.lock`，視 synthesize 為 single-writer（同一時間只有一個 synthesize subprocess 跑，由 evolve.increment_synth_counter 透過計數器 + `subprocess.Popen` 啟動瞬間立即釋放 synth_state.lock 隱性保證）。風險：evolve.increment_synth_counter 與 synthesize._save_synth_state 真並發時計數器可能漏 1，但發生機率極低且不致命。決策日期 2026-04-30（取代原本「追加 M2.1-C 套 lock」的方向，因為加 lock 會違反 §5 鎖序或卡 evolve timeout=5s）。

### 對 memory_audit.py（追加任務 M1.1-C）

- `run()` 主流程整段包進 `with FileLock(memory_lock_path, ...)`。
- 移除 `_remove_from_memory_index` 內的 `FileLock(memory_index.lock)`（line 97-99）—— 改假設呼叫者已持鎖。
- `_archive_file`、`_archive_oldest_thoughts` 內的子操作不再自取鎖。

### 對 knowledge_writer.py

不需修改：呼叫端會持鎖。但在 module docstring 補一行：「本模組函式假設呼叫者已持有 `data/memory.lock`，模組內部不取鎖。」

## 8. 驗收情境

完成 M2.1-B + M1.1-C 後，下列情境必須通過：

1. **同時觸發**：開兩個 process，一個 `python src/memory_audit.py`、一個 `python src/synthesize.py`，後啟的應 log `memory.lock busy, skipping`（audit 端）或在取鎖點等待（synthesize 端，因 timeout=30 會拿到）。MEMORY.md 內容無錯亂、無重複條目、無遺漏條目。

2. **stale lock 自動恢復**：手動 `touch -t 200001010000 data/memory.lock` 模擬 26 年前的 stale lock，跑 `python src/memory_audit.py`，應正常取鎖完成。

3. **deadlock 不觸發**：跑 `python src/synthesize.py` 完整一輪，無 `_increment_synth_counter` → `memory.lock` → `synth_state.lock` 的反向嵌套警告。

## 9. 已知限制

- `evolve._run_backup` 跑 robocopy/rsync 期間如果 audit 在 archive 檔，rsync 可能讀到半移狀態檔。實務影響：backup 副本內可能少一兩個檔，下次 backup 修正。不在本 protocol 處理。
- `synth_state.json` 的並發保護不完整（只有 evolve 一處取鎖）：**by design**，synthesize 視為 single-writer，詳見 §7「對 synthesize.py」段末註腳。
- `~/.claude/skills/` 並無任何 lock，多 synthesize 實例同時跑會撞檔。當前由「synthesize 由 evolve 串聯觸發、不重入」隱性保證，未來若改為直接定時觸發需重新設計。
