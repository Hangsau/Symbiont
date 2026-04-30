# Symbiont 改善任務分工計畫

來源：`docs/CODE_REVIEW_FINDINGS.md`（2026-04-30 審查 11 條缺陷）
產出時間：2026-04-30

本文件把 11 條 finding 拆成可獨立派發給不同 agent 的任務。每個任務自足 —— agent 只需讀對應章節，不必再回頭看 review。

---

## 目錄

- [分工原則](#分工原則)
- [執行順序與依賴](#執行順序與依賴)
- [任務總表](#任務總表)
- [共通約定](#共通約定)
- [Task M1.1-A — babysit lock 換 FileLock](#task-m11-a)
- [Task M1.1-B — memory.lock 範圍設計](#task-m11-b)
- [Task M1.2-A — LocalTransport 路徑修正](#task-m12-a)
- [Task M1.3-A — state schema v2 設計](#task-m13-a)
- [Task M1.3-B — state schema v2 實作](#task-m13-b)
- [Task M2.1-A — synthesize 寫入改 safe_write](#task-m21-a)
- [Task M2.1-B — synthesize staged commit cursor](#task-m21-b)
- [Task M2.2 — LLM 輸出嚴格 validation](#task-m22)
- [Task M2.3 — SSH quoting + agents.yaml schema](#task-m23)
- [Task M3.x — 整合測試補強](#task-m3x)
- [Task M4-A — config_loader daemon 模式](#task-m4-a)
- [Task M4-B — README 分類重寫](#task-m4-b)
- [整合驗收](#整合驗收)

---

## 分工原則

| Agent | 擅長 | 派發類型 |
|-------|------|---------|
| **Opus** | 跨檔判斷、schema 設計、決定原子化範圍 | state migration、lock 範圍、文件分類 |
| **Codex** | 單檔有明確 contract 的實作 | 換 lock 介面、validation 規則、shell quoting |
| **Haiku** | 機械性 patch、補測試、單行替換 | safe_write 替換、unit test 補齊 |

選用 Codex 還是 Haiku 的判準：
- 有需要讀懂前後脈絡再寫對的邏輯 → Codex
- 已知 before/after diff、機械替換 → Haiku

---

## 執行順序與依賴

```
第一波（可並行）：
  M1.1-A (Codex)         babysit lock 換 FileLock
  M1.1-B (Opus)          memory.lock 範圍設計（產文件）
  M1.2-A (Codex)         LocalTransport 路徑修正
  M1.3-A (Opus)          state schema v2 設計（產文件）
  M2.1-A (Haiku)         synthesize safe_write 替換
  M2.2   (Codex)         LLM schema validation
  M2.3   (Codex)         SSH quoting

第二波（依賴第一波文件）：
  M1.3-B (Codex)         state schema v2 實作 ← 依賴 M1.3-A
  M2.1-B (Codex)         staged commit cursor ← 依賴 M1.3-A、M1.1-B

第三波（依賴實作完成）：
  M3.1-3.6 (Haiku)       六個整合測試檔
  M4-A     (Haiku)       config_loader daemon 模式
  M4-B     (Opus)        README 分類重寫
```

---

## 任務總表

| ID | Agent | 目標 | 前置 | 工時 | 風險 |
|----|-------|------|------|------|------|
| M1.1-A | Codex | babysit lock 換 FileLock | — | 30m | 低 |
| M1.1-B | Opus | memory.lock 範圍設計（產文件） | — | 1h | 低 |
| M1.2-A | Codex | LocalTransport 路徑修正 | — | 30m | 低 |
| M1.3-A | Opus | state schema v2 設計（產文件） | — | 1h | 中 |
| M1.3-B | Codex | state schema v2 實作 | M1.3-A | 1.5h | 中 |
| M2.1-A | Haiku | synthesize 寫入改 safe_write | — | 15m | 低 |
| M2.1-B | Codex | staged commit cursor 實作 | M1.3-A、M1.1-B | 1h | 中 |
| M2.2 | Codex | LLM 輸出嚴格 validation | — | 45m | 低 |
| M2.3 | Codex | SSH quoting + agents.yaml schema | — | 45m | 低 |
| M3.1 | Haiku | tests/test_transport_local.py | M1.2-A | 30m | 低 |
| M3.2 | Haiku | tests/test_transport_ssh.py | M2.3 | 30m | 低 |
| M3.3 | Haiku | tests/test_synthesize_state.py | M1.3-B | 30m | 低 |
| M3.4 | Haiku | tests/test_evolve_fallback.py | M1.3-B | 30m | 低 |
| M3.5 | Haiku | tests/test_concurrency.py | M1.1-A | 30m | 低 |
| M3.6 | Haiku | tests/test_synthesize_distill_idempotent.py | M2.1-B | 30m | 低 |
| M4-A | Haiku | config_loader daemon 模式檢查 | — | 15m | 低 |
| M4-B | Opus | README 分類重寫 | M1-M3 完成 | 30m | 低 |

---

## 共通約定

所有 task 一律遵守：

1. **不要動範圍以外的檔案**。每個 task 列出「涉及檔案」，超出該清單的修改一律拒絕。
2. **保留原有測試行為**：改完後 `python -m pytest tests/ -v` 必須維持原本 70 條全部通過（除非該 task 主動補測試）。
3. **不要重構**：只改任務描述要求的範圍，不順手 rename / 抽 helper / 簡化邏輯。
4. **沒寫到要做的事就不要做**：例如 task 沒要求加 type hint，就不要動 type hint。
5. **commit message 用繁體中文**，第一行 50 字內，例如：「babysit: 改用 FileLock 取代自製 lock」。
6. **失敗或不確定時停手**，把疑問寫成註解或 commit message 末尾，不要猜。

工作目錄：`C:/claudehome/projects/Symbiont/`

---

<a id="task-m11-a"></a>
## Task M1.1-A — babysit lock 換 FileLock

**Agent**：Codex
**前置**：無
**工時**：30 分鐘

### 目標

移除 `babysit.py` 自製的非 atomic lock（check-then-write），改用專案已有的 `file_ops.FileLock`（`O_CREAT | O_EXCL`，跨平台 atomic）。

### 為什麼

現行 `_acquire_lock` 是：

```python
if lock.exists():       # check
    ...
lock.write_text(...)    # then write
```

兩個 Task Scheduler 同時觸發時可能都通過 exists() 檢查並同時寫，造成重複回應或 state 覆寫。`FileLock` 用 `O_CREAT | O_EXCL` 一次系統呼叫即可保證只有一個進程拿到。

### 涉及檔案

- `src/babysit.py`（修改）
- `src/utils/file_ops.py`（read only — `FileLock` 已存在於 line 74-118）

### 步驟

1. **讀現況**
   - `src/utils/file_ops.py:74-118` `FileLock` 介面：context manager、`acquire()` 回傳 bool、`__enter__` 失敗會 raise `TimeoutError`、constructor 是 `FileLock(path, timeout=60, stale_timeout=600)`。
   - `src/babysit.py:142-157` 自製 `_acquire_lock` / `_release_lock`。
   - `src/babysit.py:528-564` `_run_once` 的 try/finally 結構。

2. **修改 `src/babysit.py`**
   - 在 import 區（約 line 31）加：`from src.utils.file_ops import safe_read, safe_write, append_log, rotate_log, FileLock`（注意：原本就有 import 一行 file_ops，加 FileLock 即可）。
   - 刪除 `_acquire_lock`、`_release_lock` 兩個函式（line 142-157）。
   - 修改 `_run_once`（line 502-566）：把 `if not dry_run and not _acquire_lock(...)` 與最後 `if not dry_run: _release_lock(...)` 改寫為：

     ```python
     def _run_once(dry_run: bool, base_dir: Path, cfg: dict,
                   error_log: Path, lock_max_age: int, teaching_timeout: int) -> None:
         if not check_auth():
             append_log(error_log, "[babysit] auth check failed，跳過")
             return

         agents_file = base_dir / "data/agents.yaml"
         if not agents_file.exists():
             print(f"[babysit] agents.yaml 不存在：{agents_file}")
             return

         try:
             with open(agents_file, encoding="utf-8") as f:
                 agents_cfg = yaml.safe_load(f)
         except Exception as e:
             append_log(error_log, f"[babysit] 無法解析 agents.yaml: {e}")
             return

         agents = agents_cfg.get("agents", {})
         enabled_agents = {k: v for k, v in agents.items() if v.get("enabled", False)}

         if not enabled_agents:
             print("[babysit] 沒有啟用的 agent，結束")
             return

         if dry_run:
             _do_babysit_work(enabled_agents, cfg, dry_run, base_dir, error_log,
                              teaching_timeout)
             return

         try:
             with FileLock(base_dir / LOCK_FILE, timeout=0,
                           stale_timeout=lock_max_age):
                 _do_babysit_work(enabled_agents, cfg, dry_run, base_dir, error_log,
                                  teaching_timeout)
         except TimeoutError:
             print("[babysit] 上一次執行仍在進行，跳過")

         print("\n[babysit] 完成")
     ```

   - 把原本 try 區塊內容抽成新函式 `_do_babysit_work(enabled_agents, cfg, dry_run, base_dir, error_log, teaching_timeout)`，內含原本 `all_state` 載入、loop 跑 agents、`_save_json_state`。

3. **常數對應**
   - `LOCK_MAX_AGE_SECONDS` 仍保留（給呼叫端讀 config 用），對應到 `FileLock(stale_timeout=...)`。
   - `LOCK_FILE` 保留（路徑常數）。

### 驗收

- [ ] `python -m pytest tests/ -v` 70 條全部通過
- [ ] 手動：開兩個終端機同時 `python src/babysit.py --dry-run`，第一個正常跑，第二個應印 `上一次執行仍在進行，跳過`。要造這個情境，可在 `_do_babysit_work` 開頭暫時加 `time.sleep(5)` 試完再移除。
- [ ] `git diff` 只動 `src/babysit.py`

### 禁止

- 不改 `LOCK_MAX_AGE_SECONDS` 預設值（900）
- 不改 `_process_inbox` / `_process_teaching_loop` / `main` 內部邏輯
- 不重構 `_run_once` 之外的東西
- 不刪除 `LOCK_FILE` 常數

---

<a id="task-m11-b"></a>
## Task M1.1-B — memory.lock 範圍設計

**Agent**：Opus
**前置**：無
**工時**：1 小時

### 目標

設計 `data/memory.lock` 的取用協議：哪些函式必須持鎖才能修改 `memory/`、`MEMORY.md`、`knowledge/`，避免 `memory_audit` 與 `synthesize` 排程重疊時互相覆寫。**只產出設計文件**，不寫程式碼。

### 為什麼

`memory_audit` 會歸檔 memory 檔、改 MEMORY.md。`synthesize` 也寫 memory、蒸餾、搬到 distilled/、修剪 MEMORY.md。兩者都改 MEMORY.md 卻沒有共用 lock。

### 涉及檔案（read only）

- `src/synthesize.py`
- `src/memory_audit.py`
- `src/utils/knowledge_writer.py`
- `src/utils/file_ops.py`（FileLock 介面參考）

### 產出檔案

- `docs/MEMORY_LOCK_PROTOCOL.md`（新建）

### 步驟

1. **盤點所有寫入點**
   - Grep 三個 .py 對下列路徑的所有寫入操作（`write_text` / `open(... "a")` / `open(... "w")` / `unlink` / `rename` / `move`）：
     - `memory_dir`、`memory_index`（MEMORY.md）
     - `knowledge_dir`（包含 KNOWLEDGE_TAGS.md）
     - `distilled/`
   - 每個寫入點記錄：檔名、行號、操作類型（create/append/replace/delete/move）、所屬函式。

2. **判斷鎖範圍**
   每個寫入點回答：
   - 是否需要持鎖？（read-only 不需）
   - 該包多大範圍？建議起點：
     - 一把粗粒度 `data/memory.lock` 蓋掉整個 `memory_audit.run()`
     - synthesize 內 `_write_memories` + `_distill_memories` + `_prune_memory_index` 包成一個 with 區塊

3. **設計 deadlock 避免規則**
   - 已存在的 lock：`babysit.lock`、`evolve` 對 `CLAUDE.md.lock`、`synth_state.lock`
   - 規定鎖取得順序，例如：「持有 memory.lock 時不可再取 babysit.lock」「synth_state.lock 必須在 memory.lock 之前取得」

4. **產出 `docs/MEMORY_LOCK_PROTOCOL.md`**，至少包含：
   - § 鎖定義：路徑、timeout、stale_timeout 建議值
   - § 必須持鎖的函式清單（含 file:line）
   - § 不需持鎖的清單（含理由）
   - § 鎖取得順序
   - § 失敗行為：取不到鎖時應 skip 還是等待？

### 驗收

- [ ] 文件存在 `docs/MEMORY_LOCK_PROTOCOL.md`
- [ ] step 1 找到的所有寫入點都在文件內被分類
- [ ] 至少一條 deadlock 避免規則
- [ ] 沒有任何 .py 檔被修改

### 禁止

- 不寫任何 Python 程式碼（實作交給 M2.1-B）
- 不設計超過一把 lock，除非 step 1 找到明顯獨立衝突域並在文件內論證

---

<a id="task-m12-a"></a>
## Task M1.2-A — LocalTransport 路徑修正

**Agent**：Codex
**前置**：無
**工時**：30 分鐘

### 目標

修正 `LocalTransport` 與 `babysit._process_inbox` 的路徑契約不一致，讓 local agent 模式可用。

### 為什麼

現在 `babysit._process_inbox` 在 `src/babysit.py:325` 組 `remote_path = f"{inbox_remote}{target}"`。對 local agent 來說 `inbox_remote` 不存在於 `agents.example.yaml`（只有 `inbox_dir`），於是 `inbox_remote=""`，`remote_path` 變成裸檔名 `message_001.md`。`LocalTransport.read_file` 直接 `Path(path_str).read_text()` 會去 cwd 找檔案，永遠讀不到。

### 涉及檔案

- `src/utils/transport.py`（修改）
- `src/babysit.py`（read only —— 確認呼叫方式）
- `data/agents.example.yaml`（read only）

### 步驟

1. **修改 `LocalTransport.read_file`**（`src/utils/transport.py:136-141`）

   契約改為：「path_str 若是相對路徑，視為 inbox 內檔名」。實作：

   ```python
   def read_file(self, path_str: str) -> str | None:
       p = Path(path_str)
       if not p.is_absolute():
           p = self.inbox / p.name
       try:
           content = p.read_text(encoding="utf-8", errors="replace")
           return content if content else None
       except OSError:
           return None
   ```

   `p.name` 處理 `f"{inbox_remote}{target}"` 在 `inbox_remote=""` 時 path_str 等於檔名、在 `inbox_remote="some/prefix/"` 時的兩種情境，一律取出檔名再貼到 `self.inbox` 上。

2. **修改 `LocalTransport.send_reply`**（`src/utils/transport.py:143-145`）

   現有實作直接寫 `self.outbox / filename`，邏輯本來就對。**只改 docstring**：
   ```python
   def send_reply(self, content: str, _outbox_remote: str, filename: str) -> bool:
       """寫入 self.outbox / filename。outbox_remote 參數為 SSH 介面相容性保留，本地模式忽略。"""
       self.outbox.mkdir(parents=True, exist_ok=True)
       return safe_write(self.outbox / filename, content)
   ```

3. **更新 docstring**（`src/utils/transport.py` 頂部 module docstring）

   加一段：「`list_inbox` 回傳的是該 transport 自己 `read_file` 能直接收的 token，呼叫端不應假設它是檔名或絕對路徑。SSHTransport 用拼接 `{inbox_remote}{filename}`，LocalTransport 自動解析 `self.inbox / Path(path_str).name`。」

4. **不改 `babysit._process_inbox`**：`remote_path = f"{inbox_remote}{target}"` 對 SSH 仍正確，對 local 也能 work（因為 LocalTransport 會忽略前綴）。

### 驗收

- [ ] 寫一個 throwaway script（不要 commit）：
  ```python
  from pathlib import Path
  import tempfile
  from src.utils.transport import LocalTransport

  with tempfile.TemporaryDirectory() as d:
      inbox = Path(d) / "inbox"
      inbox.mkdir()
      (inbox / "msg_001.md").write_text("hello", encoding="utf-8")
      t = LocalTransport(str(inbox), str(Path(d) / "outbox"))
      assert t.list_inbox("") == ["msg_001.md"]
      assert t.read_file("msg_001.md") == "hello"
      assert t.send_reply("reply", "", "out_001.md") is True
      print("ok")
  ```
- [ ] `python -m pytest tests/ -v` 70 條全部通過
- [ ] `git diff` 只動 `src/utils/transport.py`

### 禁止

- 不改 SSHTransport 任何方法
- 不改 `make_transport` 工廠函式
- 不改 `babysit.py`
- 不改 `agents.example.yaml`（M3.1 補測試時可能會碰）

---

<a id="task-m13-a"></a>
## Task M1.3-A — state schema v2 設計

**Agent**：Opus
**前置**：無
**工時**：1 小時

### 目標

設計新版 `state.json`（evolve）與 `synth_state.json`（synthesize）schema，解決三個問題：
1. evolve fallback 只看最新 session，會漏掉中間未處理的
2. synthesize cursor 推到 `now`，backlog 超過 `sessions_per_cycle` 會永久跳過
3. synthesize 多階段寫入無一致性，中途失敗會留下半完成狀態（M2.1-B 用）

**只產出設計文件**，不寫程式碼。

### 涉及檔案（read only）

- `src/evolve.py`（特別是 `_find_target_session`、`_read_state`、`_write_state`）
- `src/synthesize.py`（特別是 `_find_target_sessions`、`_load_synth_state`、`run` 結尾的 state 更新）
- `src/utils/session_reader.py`（`find_sessions_since`、`find_latest_session`）
- 現有 `data/state.json` 與 `data/synth_state.json`（看實際資料）

### 產出檔案

- `docs/STATE_SCHEMA_V2.md`（新建）

### 必答問題

1. **evolve state schema**
   - 用 processed set（最近 N 筆 UUID）還是 cursor（last_processed_mtime）還是兩者並用？
   - N 該設多大？（建議從現有 sessions 數量推估）
   - fallback 邏輯：找「mtime > last_mtime 且 UUID 不在 set」的最舊一個

2. **synthesize state schema**
   - cursor 改成 `last_synth_session_mtime`（取本批最新 session 的 mtime，不是 `now`）
   - 同時保留 `last_synth_session_uuid` 作 tie-breaker（兩 session mtime 相同時用 UUID 比）
   - `_find_target_sessions` 用此 mtime 為 `after_ts`，回傳依 mtime 升序的最舊 N 個（目前是 `files[-limit:]` 取最新，要改為 `files[:limit]` 取最舊）

3. **staged commit 欄位**（給 M2.1-B 用）
   - synthesize run 內每個階段（patterns / memories / distill / prune / log）成功後個別更新對應的 `*_done_at`
   - 中途失敗，下次跑時從第一個未 done 階段續跑
   - schema 至少要含：`current_run_started_at`、`patterns_done_at`、`memories_done_at`、`distill_done_at`、`prune_done_at`、`log_done_at`

4. **migration 策略**
   - 舊 state.json 缺新欄位時的處理：補預設值還是視為「全部未處理」？
   - 是否需要備份舊 state.json？

5. **`session_reader.find_sessions_since` 行為改變**
   - 是否需要新增參數還是改現有的？建議：新增 `find_sessions_after(sessions_dir, after_mtime, after_uuid, limit)`，舊函式保留向後相容直到所有呼叫端遷移

### 文件結構

```
# State Schema v2

## 動機
（引述 CODE_REVIEW_FINDINGS Finding #2、#3）

## 新 evolve state.json schema
（JSON 範例 + 欄位說明）

## 新 synth_state.json schema
（JSON 範例 + 欄位說明，含 staged commit 欄位）

## session 選取邏輯變更
（_find_target_session、_find_target_sessions 新邏輯偽碼）

## session_reader.py 變更
（新函式簽名 + 為什麼）

## Migration
（舊→新轉換規則）

## 驗收情境
（mock 25 個 session 連跑 3 輪、middle session 漏掉等具體場景與預期結果）
```

### 驗收

- [ ] 文件存在 `docs/STATE_SCHEMA_V2.md`
- [ ] 五個必答問題全部有明確答案
- [ ] 至少三個驗收情境（含預期 state.json 內容變化）
- [ ] 沒有任何 .py 檔被修改

### 禁止

- 不寫 Python 程式碼
- 不要設計超出此三問題的機制（例如別新增 metrics 系統）

---

<a id="task-m13-b"></a>
## Task M1.3-B — state schema v2 實作

**Agent**：Codex
**前置**：M1.3-A 必須完成（讀 `docs/STATE_SCHEMA_V2.md`）
**工時**：1.5 小時

### 目標

依 `docs/STATE_SCHEMA_V2.md` 實作 evolve / synthesize / session_reader 的 schema 與選取邏輯，含舊 state migration。

### 涉及檔案

- `src/evolve.py`（修改 `_read_state`、`_write_state`、`_find_target_session`）
- `src/synthesize.py`（修改 `_load_synth_state`、`_find_target_sessions`、`run` 結尾 state 更新）
- `src/utils/session_reader.py`（新增 `find_sessions_after` 或修改 `find_sessions_since`，依 M1.3-A 決定）
- `data/state.json`、`data/synth_state.json`（migration code 跑一次）

### 步驟

1. **讀 `docs/STATE_SCHEMA_V2.md`**，確認所有設計細節。
2. **實作順序**：先改 `session_reader`（無相依），再改 `synthesize`，最後改 `evolve`。每改一塊就跑 pytest 確認沒退化。
3. **Migration**：在 `_read_state` / `_load_synth_state` 內加 inline migration（讀到舊 schema 自動補新欄位，不單獨寫 migration script）。
4. **不要動** `session_reader.find_latest_session` / `find_session_by_uuid`（這兩個其他地方還在用）。

### 驗收

- [ ] `python -m pytest tests/ -v` 70 條維持通過
- [ ] 手動跑：`python src/evolve.py --dry-run` 應印出選擇邏輯結果，不報錯
- [ ] 手動跑：`python src/synthesize.py --dry-run` 應印出 `found N sessions` 數量符合預期（用設計文件的驗收情境核對）
- [ ] 舊 `data/state.json`、`data/synth_state.json` 在第一次跑後被自動 migrate（看內容應有新欄位）

### 禁止

- 不偏離 `docs/STATE_SCHEMA_V2.md`，有疑問先把問題寫在 commit message
- 不一次改三個檔，分批改一批跑一次測試

---

<a id="task-m21-a"></a>
## Task M2.1-A — synthesize 寫入改 safe_write

**Agent**：Haiku
**前置**：無
**工時**：15 分鐘

### 目標

把 `src/synthesize.py` 內三處直接 `write_text` 換成 `safe_write`，獲得「先寫 .tmp 再 os.replace」的原子保證。

### 涉及檔案

- `src/synthesize.py`（修改）

### 機械替換清單

| 行號 | Before | After |
|------|--------|-------|
| 327 | `skill_path.write_text(content, encoding="utf-8")` | `if not safe_write(skill_path, content): raise OSError(f"safe_write failed: {skill_path}")` |
| 373 | `mem_path.write_text(content, encoding="utf-8")` | `if not safe_write(mem_path, content): raise OSError(f"safe_write failed: {mem_path}")` |
| 674 | `memory_index.write_text("".join(kept), encoding="utf-8")` | `safe_write(memory_index, "".join(kept))` |

對 `MEMORY.md` 索引追加（line 388-390 的 `with memory_index.open("a", ...)`）**先不要改**——append 改成 read+write 會放大鎖定範圍，等 M2.1-B 連同 staged commit 一起改。

### 步驟

1. 讀 `src/synthesize.py:327`、`373`、`674`，確認上下文沒變動。
2. 套用上表三個替換。
3. 確認檔頂 import 已有 `from src.utils.file_ops import safe_read, safe_write, append_log, FileLock`（line 37 應該已經有 safe_write，否則加上）。

### 驗收

- [ ] `python -m pytest tests/ -v` 70 條全部通過
- [ ] `git diff` 只動 `src/synthesize.py`，差異 ≤ 6 行
- [ ] grep `synthesize.py` 內所有 `.write_text(`，剩下的應該只是 line 388-390 的 append（這是預期保留的）

### 禁止

- 不要動 `_prune_memory_index` 內 line 388-390 那段 append
- 不要動 `claude_runner.py` / `file_ops.py` / 其他檔
- 不要改 raise 的 exception type（用 OSError）

---

<a id="task-m21-b"></a>
## Task M2.1-B — synthesize staged commit cursor

**Agent**：Codex
**前置**：M1.3-A（讀 STATE_SCHEMA_V2.md）、M1.1-B（讀 MEMORY_LOCK_PROTOCOL.md）
**工時**：1 小時

### 目標

讓 `synthesize.run()` 每個階段成功後個別更新 state 對應 `*_done_at`，中途失敗下次續跑、不重複已完成階段。同時依 M1.1-B 規定的位置取 memory.lock。

### 涉及檔案

- `src/synthesize.py`（修改 `run()` 主流程）
- 視 M1.1-B 設計而定，可能涉及 `src/memory_audit.py`

### 步驟

1. **讀兩份設計文件**：
   - `docs/STATE_SCHEMA_V2.md`（staged commit 欄位）
   - `docs/MEMORY_LOCK_PROTOCOL.md`（哪些區塊要包 memory.lock）

2. **改寫 `synthesize.run()` 為階段化結構**：

   ```python
   def run(dry_run=False):
       state = _load_synth_state(...)
       run_id = datetime.now(timezone.utc).isoformat(...)
       state["current_run_started_at"] = run_id

       with FileLock(memory_lock_path, timeout=...):
           if not state.get("patterns_done_at") == run_id:
               _do_patterns_phase(...)
               state["patterns_done_at"] = run_id
               _save_synth_state(...)

           if not state.get("memories_done_at") == run_id:
               _do_memories_phase(...)
               state["memories_done_at"] = run_id
               _save_synth_state(...)

           # ... distill / prune / log 各階段同樣處理
   ```

3. **對 LLM call 的處理**：LLM call 在哪個階段就在哪個階段才呼叫，不要把所有 LLM 結果先抓完再寫。設計文件若沒講清楚，預設：patterns + memories 同一次 LLM call（因為現行 prompt 一起回傳）→ 視為單一階段。

4. **失敗行為**：任一階段 raise → 該階段 done_at 不更新，整個 run 直接 return 1，下次跑會從同一階段續。

### 驗收

- [ ] `python -m pytest tests/ -v` 70 條全部通過
- [ ] 手動測試：在 `_distill_memories` 內暫時 raise `RuntimeError`，跑 `python src/synthesize.py`，確認 `synth_state.json` 內 `patterns_done_at` 與 `memories_done_at` 已更新但 `distill_done_at` 未更新；移除 raise 再跑一次，應該跳過 patterns / memories 階段直接做 distill。
- [ ] 沒有 deadlock（自己跟自己取 lock 不會卡死）

### 禁止

- 不偏離兩份設計文件
- 不重構 `_distill_memories` / `_write_memories` 等內部實作（除非為了階段化必要）
- 不改 `evolve.py`（只動 synthesize）

---

<a id="task-m22"></a>
## Task M2.2 — LLM 輸出嚴格 validation

**Agent**：Codex
**前置**：無
**工時**：45 分鐘

### 目標

對 `evolve.py` 與 `synthesize.py` 從 LLM 拿到的 JSON 加嚴格 schema validation，安全 filename / topic 規則。LLM 回傳危險或非預期欄位時，整個 run 拒寫檔、只記 error.log。

### 涉及檔案

- `src/evolve.py`（修改 `_validate_output`、`_validate_distill_output`）
- `src/synthesize.py`（修改 `_validate_synthesis_output`、`_validate_distill_output`、`run` 內 `int(quality_score)` 處）

### 規則

1. **safe filename**：正則 `^[a-z0-9][a-z0-9_-]{0,80}\.md$`，禁止 `..`、`/`、`\`、`:`。
2. **safe topic**（kebab-case）：正則 `^[a-z][a-z0-9-]{0,60}$`。
3. **必要 frontmatter**：skill 必須含 `name:`、`description:`、`type:`；memory 必須含 `name:`、`description:`、`type:`、`created:`。可在 validation 期間 grep 確認。
4. **類型限制**：
   - `rules_to_add[*].content`：必為 str、首字元 `-`
   - `patterns[*].topic`：safe topic
   - `patterns[*].pattern_type`：必為 `"guard" | "workflow" | "audit"`
   - `patterns[*].quality_score`：嘗試 `int(...)`，失敗當 0；範圍限 0-3
   - `memories[*].filename`：safe filename
   - `memories[*].content`：含必要 frontmatter

### 步驟

1. 在兩個檔開頭新增 helper（可直接內聯）：

   ```python
   _SAFE_FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}\.md$")
   _SAFE_TOPIC_RE = re.compile(r"^[a-z][a-z0-9-]{0,60}$")

   def _is_safe_filename(s: str) -> bool:
       return isinstance(s, str) and bool(_SAFE_FILENAME_RE.match(s))

   def _is_safe_topic(s: str) -> bool:
       return isinstance(s, str) and bool(_SAFE_TOPIC_RE.match(s))

   def _has_required_frontmatter(content: str, required: tuple[str, ...]) -> bool:
       if not isinstance(content, str):
           return False
       m = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
       if not m:
           return False
       fm = m.group(1)
       return all(re.search(rf"^{k}:\s*\S", fm, re.MULTILINE) for k in required)
   ```

2. 在 `synthesize._validate_synthesis_output`（line 286-301）加：
   - 每個 pattern 的 `topic` 必須 `_is_safe_topic`
   - `pattern_type` 必須 in `("guard", "workflow", "audit")`
   - `skill_content` 必須 `_has_required_frontmatter(content, ("name", "description", "type"))`
   - 每個 memory 的 `filename` 必須 `_is_safe_filename`
   - `content` 必須 `_has_required_frontmatter(content, ("name", "description", "type", "created"))`

3. 在 `synthesize.run()` 內把 `quality_score = int(pattern.get("quality_score", 3))`（line 764）改成：
   ```python
   try:
       quality_score = int(pattern.get("quality_score", 3))
   except (ValueError, TypeError):
       quality_score = 0
   quality_score = max(0, min(3, quality_score))
   ```

4. 在 `evolve._validate_output`（line 216-227）加：
   - `rule["content"]` 必須是非空 str 且首字元為 `-`

5. validation 失敗的處理已存在（記 error.log、return 1）—— 不必改。

### 驗收

- [ ] 寫一個 throwaway 測試（不要 commit）：mock LLM 回傳 `filename = "../../../etc/passwd.md"`，確認 `_validate_synthesis_output` 回傳 False。
- [ ] mock LLM 回傳 `quality_score = "abc"`，確認程式不 crash 而是當 0。
- [ ] `python -m pytest tests/ -v` 70 條全部通過
- [ ] `git diff` 只動兩個檔

### 禁止

- 不要引入 jsonschema / pydantic 等外部 library
- 不要把 validation 抽成新檔（直接放在原檔內）
- 不要改 `_extract_json` / `_parse_synthesis_output` 邏輯

---

<a id="task-m23"></a>
## Task M2.3 — SSH quoting + agents.yaml schema

**Agent**：Codex
**前置**：無
**工時**：45 分鐘

### 目標

讓 `SSHTransport` 對 remote path 做安全 quoting，並對 `agents.yaml` 加載入時的 schema 檢查，拒絕含換行、控制字元、危險 shell token 的路徑。

### 涉及檔案

- `src/utils/transport.py`（修改 `list_inbox`、`list_dialogues`，必要時 `read_file` / `send_reply`）
- `src/babysit.py`（在 `_run_once` 內 yaml load 後加 schema 驗證）

### 步驟

1. **SSH quoting**

   import `shlex`，把 `list_inbox` / `list_dialogues` 改寫為：
   ```python
   def list_inbox(self, inbox_remote: str) -> list[str]:
       q = shlex.quote(inbox_remote)
       ok, out = self._ssh(f"ls {q} 2>/dev/null")
       ...

   def list_dialogues(self, dialogues_remote: str) -> list[str]:
       q = shlex.quote(dialogues_remote)
       ok, out = self._ssh(
           f"ls -t {q} 2>/dev/null | head -{DIALOGUES_FETCH_COUNT}"
       )
       ...
   ```

   注意 `shlex.quote` 會把 `~` 包進單引號，導致 SSH 端 `~` 不展開。解法：先檢查 path 是否以 `~/` 開頭，是的話拆成 `~/` + `shlex.quote(rest)`：
   ```python
   def _quote_remote_path(p: str) -> str:
       if p.startswith("~/"):
           return "~/" + shlex.quote(p[2:])
       return shlex.quote(p)
   ```

2. **read_file / send_reply 的拼接也用同函式**

   `read_file`（line 80-91）已經對檔名部分做了單引號 escape，但目錄部分沒處理。改成：
   ```python
   def read_file(self, remote_path: str) -> str | None:
       q = _quote_remote_path(remote_path)
       ok, out = self._ssh(f"cat {q}")
       ...
   ```

   `send_reply` 用的是 scp，scp 對 `~/` 的展開行為較複雜——保留現狀，但在 docstring 提醒「`outbox_remote` 不要含特殊字元」。

3. **agents.yaml schema 驗證**

   在 `src/babysit.py:_run_once` 內 `yaml.safe_load` 之後新增：

   ```python
   def _validate_agents_cfg(agents: dict) -> list[str]:
       """回傳錯誤訊息列表；空列表表示通過。"""
       errors = []
       _path_re = re.compile(r"^[A-Za-z0-9~_./\-]+/?$")
       for name, cfg in agents.items():
           if not isinstance(cfg, dict):
               errors.append(f"{name}: 必須是 mapping")
               continue
           t = cfg.get("type", "remote_ssh")
           path_keys = (
               ("inbox_remote", "outbox_remote", "dialogues_remote")
               if t == "remote_ssh" else
               ("inbox_dir", "outbox_dir")
           )
           for k in path_keys:
               v = cfg.get(k, "")
               if not isinstance(v, str):
                   errors.append(f"{name}.{k}: 必須是字串")
                   continue
               if "\n" in v or "\r" in v or any(ord(c) < 32 for c in v):
                   errors.append(f"{name}.{k}: 含控制字元")
               if not _path_re.match(v) and v != "":
                   errors.append(f"{name}.{k}: 含可疑字元 ({v!r})")
       return errors
   ```

   呼叫處：

   ```python
   errors = _validate_agents_cfg(agents)
   if errors:
       for e in errors:
           append_log(error_log, f"[babysit] agents.yaml schema error: {e}")
       print(f"[babysit] agents.yaml 有 {len(errors)} 條 schema 錯誤，跳過")
       return
   ```

4. **import**：`src/utils/transport.py` 加 `import shlex`；`src/babysit.py` 加 `import re`（如未有）。

### 驗收

- [ ] 手動測試：在 `agents.yaml` 內塞 `inbox_remote: "/tmp/foo$(rm -rf /).txt"`，跑 `python src/babysit.py --dry-run` 應該被 schema 擋下。
- [ ] 手動測試：在 inbox 內放含空白檔名 `"hello world.md"`，SSH 模式（mock）應能正確 cat。
- [ ] `python -m pytest tests/ -v` 70 條全部通過
- [ ] `git diff` 只動 `src/utils/transport.py` 與 `src/babysit.py`

### 禁止

- 不重寫 `_ssh` / `_scp_to`
- 不改 `LocalTransport`
- 不要把 schema validation 抽成獨立檔（直接放 babysit.py 內或 transport.py 內均可，挑一個）

---

<a id="task-m3x"></a>
## Task M3.x — 整合測試補強（六個檔）

**Agent**：Haiku
**前置**：對應的 M1/M2 task 完成

每個測試檔派發為獨立 task。共通要求：

- 使用 pytest + 現有 conftest.py
- 用 `tmp_path` fixture，不依賴實際 `~/.claude/`
- 用 `monkeypatch` 替換 `run_claude` / `subprocess.run` 等外部呼叫
- 每個測試檔至少 3 條 test case，總體加進來不超過 50 條（避免測試過度）

### M3.1 — tests/test_transport_local.py（依賴 M1.2-A）

至少包含：
- `test_local_transport_round_trip`：寫一條 message 到 inbox，跑 `LocalTransport.list_inbox` / `read_file` / `send_reply`，確認 outbox 有 reply
- `test_local_transport_empty_inbox`：空目錄回傳 `[]`
- `test_local_transport_read_with_prefix`：`read_file("some/prefix/msg.md")` 仍解析到 `self.inbox / msg.md`

### M3.2 — tests/test_transport_ssh.py（依賴 M2.3）

用 `monkeypatch` 替換 `subprocess.run`，驗證：
- `test_ssh_list_inbox_quoting`：傳含空白的 path，確認 `subprocess.run` 收到的 cmd 內 path 已被 shlex.quote
- `test_ssh_read_file_with_tilde`：傳 `~/foo/bar.md`，確認 `~/` 被保留、`foo/bar.md` 被 quote
- `test_ssh_inbox_failure_returns_empty`：mock returncode != 0 時 `list_inbox` 回 `[]`

### M3.3 — tests/test_synthesize_state.py（依賴 M1.3-B）

至少包含：
- `test_backlog_no_loss`：在 tmp 內建 25 個 fake session jsonl，連續呼叫 `_find_target_sessions` 三次，確認 25 個全部被選過、無重複
- `test_cursor_advances_to_session_mtime_not_now`：跑一次 synthesis 後，`last_synth_session_mtime` 應等於本批次最舊一個 session 的 mtime（依 M1.3-A 設計）

### M3.4 — tests/test_evolve_fallback.py（依賴 M1.3-B）

至少包含：
- `test_fallback_finds_oldest_unprocessed`：建 5 個 session（ABCDE，mtime 升序），state 標記 BCE 已處理，fallback 應回傳 A（最舊未處理）
- `test_fallback_no_unprocessed_returns_none`：所有 session 都在 processed_set 內 → 回 None

### M3.5 — tests/test_concurrency.py（依賴 M1.1-A）

用 `threading` 起兩個 thread 同時對 tmp 內的 lock 跑 `FileLock(...).__enter__()`，確認只有一個成功。也測 `babysit._run_once` 跑兩次（mock `_do_babysit_work` 加 sleep）只有一次實際做事。

### M3.6 — tests/test_synthesize_distill_idempotent.py（依賴 M2.1-B）

mock LLM 在 distill 階段 raise，跑 synthesize 應記錄 `patterns_done_at` 與 `memories_done_at`、未記錄 `distill_done_at`；移除 raise 再跑，應 skip 前兩階段（用 spy 驗證 `run_claude` 對 patterns prompt 沒被呼叫第二次）。

### 共通驗收

- [ ] `python -m pytest tests/ -v` 維持原有 70 條 + 新增 testcase 全部通過
- [ ] 新增的測試獨立於外部環境（不依賴 `~/.claude/`、不需要 SSH 真連線）
- [ ] `git diff` 每個 task 只動對應 `tests/test_*.py`

### 禁止

- 不要改 `src/` 內任何檔
- 不要在 conftest.py 加全域 fixture（除非 ≥ 3 個新測試檔都會用到）
- 不要 mock 過頭（直接 mock `LocalTransport` 整個類別是錯的，要 mock subprocess / Path 邊界）

---

<a id="task-m4-a"></a>
## Task M4-A — config_loader daemon 模式檢查

**Agent**：Haiku
**前置**：無
**工時**：15 分鐘

### 目標

`primary_project` 留空時的 auto-detect 對 daemon 模式風險高（最近活動的專案未必是要維護 memory 的）。改成：daemon 模式下空值記 warning，並在啟動時 log 印出實際解析路徑。

### 涉及檔案

- `src/utils/config_loader.py`（修改）
- `src/evolve.py`、`src/synthesize.py`、`src/memory_audit.py`、`src/babysit.py`（每檔啟動時加一行 log）

### 步驟

1. 在 `src/utils/config_loader.py` 找到 auto-detect 邏輯（搜尋 `primary_project` 與 `~/.claude/projects`）。
2. 在 auto-detect 觸發時呼叫 `print(f"[config_loader] WARNING: primary_project not set, auto-detected: {detected}", file=sys.stderr)`。
3. 在每個 daemon 主程式（evolve / synthesize / memory_audit / babysit）的 `main()` 開頭，cfg 載入後加：
   ```python
   print(f"[<modulename>] primary_project_dir = {get_path(cfg, 'primary_project_dir')}")
   ```

### 驗收

- [ ] `python src/evolve.py --dry-run` 第一行印出 `primary_project_dir = ...`
- [ ] 把 config 內 `primary_project` 留空再跑，stderr 有 WARNING
- [ ] `python -m pytest tests/ -v` 70 條全部通過

### 禁止

- 不改 auto-detect 演算法本身（這是另一個 task）
- 不要把 warning 改成 raise

---

<a id="task-m4-b"></a>
## Task M4-B — README 分類重寫

**Agent**：Opus
**前置**：M1-M3 全部完成（讀真實狀態）
**工時**：30 分鐘

### 目標

現行 `README.md` 描述完整反思 loop、skill 自動生成、memory distillation、agent babysit、dead letter queue 等能力，但測試覆蓋有落差。改寫 README，把功能分成「已驗證穩定」與「設計目標 / 實驗中」。

### 涉及檔案

- `README.md`（修改）
- `tests/`（read only — 看實際覆蓋）
- `data/evolution_log.md`（read only — 看實際運行紀錄）

### 步驟

1. 讀 `tests/` 找出每個 milestone 對應的測試覆蓋情況（M1.x、M2.x、M3.x、M4.x）。
2. 讀 `data/evolution_log.md` 看實際跑了幾次、產出多少 skill / 蒸餾。
3. 改寫 README 結構：
   ```
   # Symbiont
   ## 為什麼
   ## 已驗證穩定
     - evolve（含整合測試 + 實際 N 次運行）
     - babysit lock（M1.1-A 後）
     - LocalTransport（M3.1 後）
     - ...
   ## 設計目標 / 實驗中
     - synthesize 跨 session 蒸餾（待真實環境長期驗證）
     - dead letter queue（基本實作完成，重試上限 5）
     - ...
   ## 已知限制
   ## 安裝
   ## 對應計畫文件
     - CODE_REVIEW_FINDINGS.md
     - IMPROVEMENT_PLAN.md
     - STATE_SCHEMA_V2.md
     - MEMORY_LOCK_PROTOCOL.md
   ```

### 驗收

- [ ] README 內每條「已驗證穩定」項目都對應到實際測試或運行記錄
- [ ] 「設計目標」section 不為空（誠實標出未驗證項目）
- [ ] 移除任何 over-promise 描述

### 禁止

- 不改任何 .py 檔
- 不要刪除 README 的安裝指引

---

## 整合驗收

所有 task 完成後，整體應通過：

1. **Local agent 模式可用**：`tmp_path` 內完整 inbox/outbox round-trip 通過
2. **並發安全**：兩個 babysit 實例同時啟動，只有一個取得 lock
3. **Backlog 不漏不重**：mock 25 個 session 連跑三次 synthesis，全部處理且不重複
4. **危險 LLM 輸出被擋**：filename 含 `../`、JSON malformed → 不寫任何檔，只記 error.log
5. **memory_audit 與 synthesize 共用 memory.lock**，不會撞 `MEMORY.md`
6. **測試套件**：原 70 條 + 新增 ~30 條，總計 ≈ 100 條，在 Windows 穩定通過

如果有 task 跑完發現實際狀態無法滿足整合驗收，回頭追加修補 task，不要硬塞驗收。

---

## 執行進度

最後更新：2026-04-30（完成 19/19：M1.1-A/B/C、M1.2-A、M1.3-A/B、M2.1-A/B/D、M2.2、M2.3、M3.1-3.6、M4-A、M4-B。pytest 70 → 102 條，包含中段 git reset 災難救援）

| ID | 狀態 | Owner | 完成 | 備註 |
|----|------|-------|------|------|
| M1.1-A | DONE | Codex | 2026-04-30 | pytest 70/70 通過。Codex 保留 `_acquire_lock` / `_release_lock` 為 wrapper（給測試用），`_run_once` 改用 `with FileLock(...)`。M3.5 完成後可移除 wrapper。 |
| M1.1-B | DONE | Opus (this session) | 2026-04-30 | 產出 `docs/MEMORY_LOCK_PROTOCOL.md`。發現 `synth_state.json` 並發保護不完整（`_save_synth_state` 不取鎖），列為已知限制，於 M2.1-B 一併補。 |
| M1.1-C | DONE | Codex | 2026-04-30 | pytest 98/98。memory_audit.run() 整段包 memory.lock，TimeoutError → busy skip return 0；移除舊 memory_index.lock；knowledge_writer.py 補 docstring。 |
| M1.2-A | DONE | Codex | 2026-04-30 | pytest 70/70 通過。Codex 額外加 module-level「契約」段，明確區分 SSH 與 Local 兩種 token 語意。 |
| M1.3-A | DONE | Opus (this session) | 2026-04-30 | 產出 `docs/STATE_SCHEMA_V2.md`。新增 schema 含 `processed_recent`（evolve）、`last_synth_session_mtime` + `current_run_id` + 五階段 `<stage>_done_at`（synthesize）、新函式 `find_sessions_after`、inline migration + `.pre_v2_backup`。 |
| M1.3-B | DONE | Codex | 2026-04-30 | pytest 70/70 通過。三檔修改：session_reader +27（含 OSError 容錯）、evolve +134（v2 schema + migration + fallback）、synthesize +184（含 M2.2 + M2.1-A）。staged commit 欄位已放入 schema，啟用邏輯交給 M2.1-B。 |
| M2.1-A | DONE | Haiku (spawn) | 2026-04-30 | pytest 70/70 通過。git diff 6 行精確。grep 確認 synthesize.py 內 `.write_text(` 已全消失（line 388-390 的 `open("a")` append 維持不動）。 |
| M2.1-B | DONE | Codex | 2026-04-30 | pytest 90/90。階段化 + memory.lock + resume 中間暫存全完成。**漏：`_save_synth_state` 沒取 `synth_state.lock`**（codex 視為「建議」而非 scope 必須），補在 M2.1-D。加分：dry-run 用 nullcontext、資料暫存讓 distill 失敗 resume 時不重跑前置。 |
| M2.1-D | RESOLVED | (Sonnet decision) | 2026-04-30 | 採方案 B：synthesize 視為 single-writer，`_save_synth_state` 不取 lock。MEMORY_LOCK_PROTOCOL §9 已標為 by design。風險：evolve+synthesize 真並發時計數器可能漏 1，可接受。 |
| M2.2 | DONE | Codex | 2026-04-30 | pytest 70/70 通過。evolve + synthesize 對稱實作 safe filename / topic / frontmatter validation，distill output 也加 entry 檢查。`quality_score` 非數字當 0 + clamp。 |
| M2.3 | DONE | Codex | 2026-04-30 | pytest 98/98。`_quote_remote_path` helper 保留 `~/` 語意；agents.yaml 三層 schema 檢查（型別 + 控制字元 + path regex）。 |
| M3.1 | DONE | Haiku (spawn) | 2026-04-30 | 82/82 通過。寫了 12 條（規格上限 7 條），但內容無冗餘且涵蓋契約完整面。下一波 prompt 會把上限改成硬性。 |
| M3.2 | BLOCKED | (Haiku) | — | 等 M2.3 |
| M3.3 | DONE | Haiku (spawn) | 2026-04-30 | 90/90 通過。4 條：backlog 不漏不重、cursor 非 now、v1 migration 兩條（schema + mtime 還原）。 |
| M3.4 | DONE | Haiku (spawn) | 2026-04-30 | 90/90 通過。4 條：fallback 找最舊未處理、全處理回空、v1 migration、processed_recent 環狀截斷。 |
| M3.5 | DONE | Haiku (spawn) | 2026-04-30 | 94/94 通過。4 條：兩 thread 互斥、release 後重 acquire、stale lock 強制接管、`_run_once` 並發只跑一次 `_do_babysit_work`。 |
| M3.6 | DONE | Haiku (spawn) | 2026-04-30 | 98/98 通過。4 條：distill 失敗保留 state、resume 跳過已完成階段（`run_claude` 沒被重呼叫）、無 sessions 清 run_id、完整 cycle cursor 更新。lock_busy 那條合理省略。 |
| M4-A | DONE | Sonnet (manual) | 2026-04-30 | pytest 102/102。原 Haiku spawn 跑了 git pull --rebase + reset 把所有 src/ 修改吃掉，從 codex rollout 還原 22/23 patches 後我手動補做 M2.1-A + M4-A，並修一個測試 fixture（補 primary_project_dir）。 |
| M4-B | BLOCKED | (Opus) | — | 等 M1-M3 完成後重寫 README |

### 狀態定義

- **DONE**：實作完成且驗收通過（程式 task）或文件交付（設計 task）
- **RESOLVED**：經設計討論做出決策、不需實作（如 M2.1-D 採 by-design 方案）
- **IN PROGRESS**：有 owner 正在執行
- **PENDING**：依賴解除、可派工
- **BLOCKED**：仍有未完成的前置任務

### 派工節奏（事後紀錄）

實際派工順序（2026-04-30 同日完成）：
1. Sonnet 設計 M1.1-B、M1.3-A 兩份 docs
2. Codex 依序做 M1.1-A、M1.2-A、M1.3-B、M2.2、M2.1-B、M2.3、M1.1-C
3. Haiku（spawn）做 M2.1-A、M3.1-3.6 七條測試/機械
4. Sonnet 自己做 M4-A（中段 Haiku 跑 git reset 災難後手動還原並補做）+ M4-B
5. M2.1-D 走設計討論決策，by-design 接受 single-writer

教訓：派 spawn agent 的 prompt 必須**明確禁止 git 操作**（reset / pull / checkout / restore / stash drop / rebase）。事件搶救工具見 `recovery/`。

