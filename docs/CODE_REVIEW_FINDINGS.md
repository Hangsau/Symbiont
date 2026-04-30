# Symbiont 缺失與改進建議

審查日期：2026-04-30  
範圍：專案結構、`src/` 主流程、`src/utils/` 工具層、現有測試與文件一致性。

## 總評

Symbiont 的模組切分清楚，`evolve.py`、`synthesize.py`、`memory_audit.py`、`babysit.py` 各自負責不同背景任務，設定集中於 `config.yaml`，也已有一批純函式測試。以個人本機 automation prototype 來說，方向正確，核心概念完整。

目前主要問題不在語法或小型函式，而在 daemon 類工具最容易出事的地方：狀態游標、排程重入、跨程序鎖、transport 介面一致性、多檔寫入的一致性，以及文件承諾和實作/測試覆蓋之間的落差。若要長期自動運行，建議先補強這些基礎可靠性問題，再擴充功能。

現有測試結果：

```text
70 passed in 0.27s
```

測試通過，但目前測試集中在 pure functions，尚不足以證明排程、I/O、LLM 失敗、SSH/local transport、state migration 等實際運行路徑穩定。

## 高優先缺陷

### 1. Local transport 無法正確讀取 inbox

位置：

- `src/utils/transport.py`
- `src/babysit.py`
- `data/agents.example.yaml`

問題：

`LocalTransport.list_inbox()` 回傳的是檔名，例如 `message_001.md`。但 `babysit._process_inbox()` 會組出：

```python
remote_path = f"{inbox_remote}{target}"
content = transport.read_file(remote_path)
```

local agent 範例設定只有 `inbox_dir` / `outbox_dir`，沒有 `inbox_remote`。因此 local mode 會嘗試從目前工作目錄讀 `message_001.md`，而不是從 `inbox_dir/message_001.md` 讀取。結果是本地 agent 模式基本不可用。

建議修法：

- 讓 transport 介面一致：`list_inbox()` 回傳 transport 自己能讀的 path token。
- 或修改 `LocalTransport.read_file()`：如果傳入相對路徑，就解析成 `self.inbox / path_str`。
- 補 integration test：建立 temporary inbox/outbox，跑 `_process_inbox()`，確認 local message 能被讀到並寫出 reply。

建議優先度：P0。

### 2. synthesize 停機補跑會遺失 session

位置：

- `src/synthesize.py`
- `src/utils/session_reader.py`

問題：

`synthesize._find_target_sessions()` 根據 `last_synth_at` 找 mtime 較新的 session，最多取 `sessions_per_cycle` 個。執行結束後，`run()` 將 `last_synth_at` 設為當下時間。

如果停機或未觸發期間累積超過 `sessions_per_cycle` 個 session，這次只會分析最後 N 個，較舊但尚未分析的 session 會因 `last_synth_at` 被推到現在而永久跳過。

建議修法：

- 不要把 cursor 設為 `now`。
- 改成記錄最後實際處理 session 的 mtime 或 UUID。
- 如果 backlog 超過 limit，下一輪繼續從上一個處理點往後跑。
- state 建議包含：

```json
{
  "last_synth_session_uuid": "...",
  "last_synth_session_mtime": 1770000000.0
}
```

建議優先度：P0。

### 3. evolve fallback 只看最新 session，會跳過漏處理項目

位置：

- `src/evolve.py`
- `src/utils/session_reader.py`

問題：

沒有 `pending_evolve.txt` 時，`evolve._find_target_session()` 只取全域最新 session，並和 `state.last_processed_uuid` 比較。如果最新 session 已處理，但中間有其他 session 漏掉，就會直接判定「無新 session 需要處理」。

這和 README 中「latest unprocessed session」的描述不一致。

建議修法：

- 將 state 從單一 `last_processed_uuid` 改為 processed set 或 cursor。
- 更好的做法是維護 pending queue：Stop hook 寫入每個 session UUID，evolve 逐一消化。
- fallback 掃描時，至少找出 mtime 大於最後處理時間且未處理的最舊 session，而不是只看最新。

建議優先度：P0。

### 4. babysit lock 不是 atomic，排程重入有競態

位置：

- `src/babysit.py`
- `src/utils/file_ops.py`

問題：

`babysit._acquire_lock()` 使用：

```python
if lock.exists():
    ...
lock.write_text(...)
```

這不是 atomic。兩個 Task Scheduler 實例同時啟動時，可能都通過 `exists()` 檢查並同時執行，造成重複回覆或 state 覆寫。

專案已有 `FileLock`，使用 `os.O_CREAT | os.O_EXCL`，應統一採用。

建議修法：

- 移除 babysit 自製 lock，改用 `FileLock(base_dir / LOCK_FILE, ...)`。
- 補並發測試：兩個 thread/process 同時 acquire，確認只有一個成功。

建議優先度：P0。

## 中優先問題

### 5. synthesize 多檔寫入缺少一致性保證

位置：

- `src/synthesize.py`
- `src/utils/knowledge_writer.py`

問題：

synthesize 一次會寫入多個外部狀態：

- `~/.claude/skills/<topic>/SKILL.md`
- `memory/*.md`
- `MEMORY.md`
- `knowledge/<type>/*.md`
- `knowledge/KNOWLEDGE_TAGS.md`
- `data/synth_state.json`
- `data/evolution_log.md`

目前中間任何一步失敗，都可能留下部分成功、部分失敗的狀態。例如 skill 已寫入但 state 未更新，下次可能重跑；knowledge 已寫入但原始 memory 尚未搬移，也可能重複蒸餾。

建議修法：

- 所有重要寫入使用 `safe_write()` 或先寫 temp 再 replace。
- synthesis 每個階段回傳明確 success/failure。
- state 中記錄已完成階段或每個產物的 idempotency key。
- 對 memory/knowledge 寫入加入 lock，避免和 audit 同時修改。

建議優先度：P1。

### 6. memory_audit 和 synthesize 可能同時修改 memory

位置：

- `src/memory_audit.py`
- `src/synthesize.py`

問題：

`memory_audit` 會歸檔 memory 檔案、修改 `MEMORY.md`。`synthesize` 也會寫 memory、蒸餾 memory、搬移到 `memory/distilled/`、修剪 `MEMORY.md`。兩者目前沒有共用 lock。

如果排程重疊，可能出現：

- `MEMORY.md` append/prune 互相覆蓋。
- audit 正在歸檔的檔案被 synthesize 讀取或搬移。
- knowledge distillation 讀到半更新狀態。

建議修法：

- 新增 `memory.lock`，所有會修改 `memory/`、`MEMORY.md`、`knowledge/` 的流程都必須取得 lock。
- 將 `MEMORY.md` 修改統一封裝成 helper，避免各處自行 open append/write。

建議優先度：P1。

### 7. SSHTransport shell command quoting 不完整

位置：

- `src/utils/transport.py`

問題：

`list_inbox()` 和 `list_dialogues()` 直接拼 shell command：

```python
ls {inbox_remote} 2>/dev/null
ls -t {dialogues_remote} 2>/dev/null | head -10
```

如果 remote path 有空白、特殊字元，或設定值錯誤，會造成失敗或 shell injection 風險。雖然目前設定檔預期是受信任的本機配置，但 daemon 工具仍應避免直接拼 shell。

建議修法：

- 對 remote path 做安全 quoting。
- 或要求 remote path 只能是預先驗證過的簡單 path。
- 對 `agents.yaml` 做 schema validation，拒絕含換行、控制字元、危險 shell token 的路徑。

建議優先度：P1。

### 8. LLM 輸出 schema 驗證不足

位置：

- `src/evolve.py`
- `src/synthesize.py`

問題：

目前 JSON parser 有基本容錯，但 schema 驗證偏寬鬆。例如：

- `rules_to_add[*].content` 不檢查是否字串。
- `topic` 沒有限制 kebab-case。
- `filename` 沒有限制副檔名或禁止 `../`。
- `skill_content` frontmatter 不驗證必要欄位。
- `quality_score = int(...)` 若 LLM 回傳非數字字串，可能直接拋例外。

建議修法：

- 對 LLM output 建立嚴格 schema validation。
- 限制 filename/topic 只能使用安全字元。
- skill/memory 寫入前驗證 frontmatter 必要欄位。
- 所有 LLM 欄位轉型都要用 try/except，失敗只記 error log，不寫檔。

建議優先度：P1。

## 低優先與維護性問題

### 9. 文件描述比實際測試覆蓋更完整

位置：

- `README.md`
- `docs/*.md`
- `tests/`

問題：

README 描述了完整反思 loop、skill 自動生成、memory distillation、agent babysit、dead letter queue 等能力。但測試多集中於 pure functions，對真實流程缺少驗證。

建議補測：

- `evolve.run(dry_run=True)` 對 pending session 的選擇。
- `evolve` JSON parse 失敗時不寫 `CLAUDE.md`、不更新 state。
- `synthesize` backlog 超過 limit 時不遺失。
- `LocalTransport` 完整收發。
- `SSHTransport` command generation 或 mock subprocess。
- `memory_audit` 和 `synthesize` 對 `MEMORY.md` 的互動。

建議優先度：P2。

### 10. config path 自動偵測容易選錯 primary project

位置：

- `src/utils/config_loader.py`

問題：

`primary_project` 留空時，系統會掃 `~/.claude/projects/` 並選最近有 session 的子目錄。這對個人互動方便，但對 daemon 來說風險較高：最近活動的專案未必是想維護 memory 的專案。

建議修法：

- 安裝流程要求明確設定 `primary_project`。
- auto-detect 只作互動 fallback，daemon 模式應拒絕空值或至少記 warning。
- 在 log 中輸出實際解析出的 `primary_project_dir`。

建議優先度：P2。

### 11. Windows/PowerShell 執行時有環境雜訊

觀察：

在目前環境執行多個 PowerShell 命令後，會出現 `Import-Clixml` 相關錯誤訊息。pytest 本身通過，但這類 shell profile 或環境輸出可能污染排程 log。

建議修法：

- 檢查 PowerShell profile、prompt theme 或 shell integration 是否輸出壞掉的 serialized data。
- 排程任務盡量使用乾淨 shell 或直接呼叫 Python executable。
- log parser 不應假設 stdout/stderr 完全乾淨。

建議優先度：P2。

## 建議修復順序

1. 修正 `LocalTransport` 路徑讀取問題，補 local transport integration test。
2. 將 babysit lock 改為 `FileLock`，補並發 acquire 測試。
3. 重做 evolve/synthesize 的 state cursor，確保 backlog 不會遺失。
4. 為 memory/knowledge 修改加共用 lock。
5. 對 LLM output 加嚴格 schema validation 與安全 filename/topic 驗證。
6. 將 skill/memory/knowledge 寫入改成 atomic/idempotent。
7. 補整合測試，讓 README 中承諾的主要流程都有最小驗證。
8. 最後再整理 README，把「已穩定支援」和「設計目標/限制」分清楚。

## 建議的驗收標準

完成上述修復後，至少應能通過以下驗收：

- local agent 模式可在 temporary directory 中完整讀取 inbox、產生 reply、寫入 outbox。
- 同時啟動兩個 babysit 實例時，只會有一個取得 lock。
- 累積 25 個未 synthesis sessions，連續跑三次 synthesis 後不遺失、不重複處理。
- LLM 回傳 malformed JSON 或危險 filename 時，不寫任何 skill/memory/knowledge，只記 error log。
- memory_audit 和 synthesize 同時觸發時，不會造成 `MEMORY.md` 覆寫或 memory 檔案搬移衝突。
- 全測試包含 pure tests 與 integration tests，並能在 Windows 環境穩定通過。
