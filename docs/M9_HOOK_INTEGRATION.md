# M9 — Hook Integration（規劃中）

> 狀態：規劃中（2026-05-03）
> 前置：M10 search_memory.py 已完成（commit 689eb41 / 669b9bf）
> 性質：M10 retrieval engine 的最後一哩 — 把 search 接到 UserPromptSubmit hook，每次 prompt 自動注入相關記憶

---

## 目標狀態

UserPromptSubmit hook 在每次 user prompt 自動呼叫 `search_memory.search()`，注入 top-N 相關記憶到 Claude Code context。包含跨 OS 安裝腳本 + 失敗 graceful 機制 + 1-2 週 self-pilot 數據，作為決策後續做 M11 / M10 內部迭代 / 完工的依據。

---

## 背景與動機

**問題**（agora Topic 03 + Topic 04 findings）：
- Agent 是純被動發現者，只有「任務語意」會觸發記憶/工具的取用
- 即使記憶在系統內，agent 不會主動 Grep — 等於沒有
- Session-start 注入是 snapshot，固定預算無法 scale

**M10 已解決的部分**：
- search_memory.py 能根據自然語言 query 找到相關記憶（concept overlap 評分）
- run_search.py CLI 入口

**M9 要解決的部分**：
- 把 search 接到「task semantic trigger」上 — UserPromptSubmit 是最直接的接點
- 每個 user prompt 就是一次任務語意訊號 → 自動觸發 search → 注入結果
- 繞過「agent 必須記得查 memory」的失敗點（agora 證實這個 pattern 不可靠）

---

## 三階段（核心 + 條件式擴張）

### Phase A.1 — Hook 實作（核心）`[規劃]`

**新建 `Symbiont/scripts/run_hook_search.py`**：
- stdin 讀 UserPromptSubmit JSON payload
- 提取 `prompt` 欄位
- import `src.search_memory.search()` 直接呼叫（不另起 subprocess）
- stdout 輸出 top-N markdown 區塊
- 全包 try/except → exit 0；錯誤寫 stderr

**`config.yaml` 新增 `m9_hook` 區塊**：
```yaml
m9_hook:
  enabled: false  # 發行版預設 false（user 安裝後手動啟用）
  min_prompt_length: 10
  top_n: 3
  timeout_seconds: 5
  stdout_format: "markdown"
```

**端到端測試矩陣**：空 / 短 / 巨長（10K+）/ 特殊字元 / null bytes / 表情符號 / 並發 3-5 instances / search 故意失敗 → 全部 hook 不 crash 不 hang

### Phase A.4 — Self-pilot（關鍵 gate）`[規劃]`

- 在開發者機器**手動改 settings.json** 啟用（安裝腳本留到 gate 通過後寫 — 先驗核心假設值得不值得，install 工程不該擋在驗證假設前）
- 啟用前先記錄 baseline：問用戶「最近 1-2 週名稱混淆 / 路徑找錯 / 不知道去哪查事件大概幾次？」
- `enabled: true`、`top_n: 3`、跑 1-2 週
- 收集：觸發次數 / 失敗次數 / overhead 平均值 / 負面事件清單（污染 context、誤導 agent、明顯變慢）
- 結束時對比事件數 vs baseline

### Decision Gate（質性比較）

self-pilot 期間「名稱混淆 / 路徑找錯 / 不知道去哪查」事件數對比啟用前 baseline：

| 條件 | 結論 |
|---|---|
| hook 穩定 + 事件數**減少** + 無負面事件 | 進 A.2/A.3（補發行工程） |
| hook 穩定 + 事件數**持平** + 無負面事件 | 進 M10 concept 展開策略迭代（search 命中率不夠） |
| 有負面事件（context 污染、誤導 agent、明顯 overhead 抱怨） | 暫停 M9，重新評估設計 |
| hook 不穩定（crash / 擋 session / 並發 race） | 修 bugs，延長 pilot |

> **不用引用率百分比**：「引用」本身難客觀量測（rephrase / 隱式使用 / 顯式 cite 算哪種？），硬給數字是假精確。質性事件數對比 baseline 更誠實。

### Phase A.2 — 跨 OS 安裝（pilot 通過後）`[規劃，條件式]`

- `Symbiont/setup/install-m9-hook.py`：
  - 偵測 OS、找 settings.json、加 UserPromptSubmit hook entry
  - 已存在則 warning 不覆蓋
  - 支援 `--dry-run`
  - **Interactive prompt**：「啟用 M9 hook？(y/N)」— 預設 N，user 確認 y 才把 config.yaml 的 `enabled` 改 true
- `Symbiont/setup/uninstall-m9-hook.py`：對應移除

### Phase A.3 — 文件對齊（pilot 通過後）`[規劃，條件式]`

- `Symbiont/CLAUDE.md` 「各程式職責速查」加 run_hook_search.py 行
- `Symbiont/HANDOFF.md` 更新 M9 狀態
- 本檔加安裝步驟、debug、disable 方法

---

## 已識別風險

### 執行時

| 風險 | 預案 |
|---|---|
| **R1** Hook 失敗擋 session（Windows 黑框、靜默失敗、跨 session 干擾） | run_hook_search.py 全包 try/except → exit 0；docs 提供一行 disable 指令；發行版預設 enabled: false |
| **R2** Overhead（每 prompt + 3-5 秒）使 user 失去信任 | config `timeout_seconds: 5` 硬上限；超時 stdout 印「(search skipped: timeout)」 |
| **R3** UserPromptSubmit stdout 進 context 機制版本依賴 | 文件標註測試的 Claude Code 版本；install 腳本檢查版本 |

### 結構性

| 風險 | 預案 |
|---|---|
| **R4** 畸形 prompt（空/巨長/null bytes/特殊字元） | run_hook_search.py 設長度上限 2K chars truncate；非 string 直接 exit 0 |
| **R5** 並發（user 連送多 prompt）+ claude -p rate limit | A.1.4 模擬 5 並發；pilot 觀察 rate limit；需要時加 file lock 或 token bucket |
| **R6** 邊界 prompt（純標點/表情/中英混亂） | min_prompt_length: 10 起點；跳過時 stdout 完全空（不印 skipped 字樣） |
| **R7** 命令注入 | search_memory.py 已用 subprocess list args；code review checklist 禁 shell=True |
| **R8** Config skip-worktree 污染（本機 enabled: true，發行版 false） | 走五步流程：no-skip → 改 false → add → commit → 改回 true → re-skip |

---

## 驗收標準

**Phase A.1 完成**：
- 測試矩陣全 pass（空/短/長/巨長/特殊字元/null bytes/表情/並發/失敗 case）
- run_hook_search.py code review 通過（subprocess list args、try/except 全包、exit 0）

**Phase A.4 完成**：
- ≥ 1 週 self-pilot 數據
- 有具體數字：觸發次數、失敗次數、引用率（手動估計）、平均 overhead
- Decision gate 條件能對應到下一步

**Phase A.2/A.3 完成（條件式）**：
- 跨 OS install 腳本 dry-run + 實裝測試 pass
- 文件對齊掃描通過（HANDOFF / CLAUDE.md / setup 三處同步）

---

## 不在此 milestone 範圍

- **M11**（CLAUDE.md audit 工具，幫 user 評估三層分層）— 等 pilot 數據後再決定是否做
- **M10 演算法改進**（concept 展開策略、min_score 邏輯）— 等 pilot 數據後再決定
- 用戶自己的 `claudehome/CLAUDE.md` / `~/.claude/CLAUDE.md` 內容設計（user content，非 Symbiont 範圍）
- M10 Phase 3 embeddings（獨立 milestone）

---

## 相關 agora findings

- **Topic 03（工具發現策略）**：agent 純被動，需求驅動 → M9 hook 把 prompt 當作 task semantic trigger，繞過被動失敗
- **Topic 04（快照認知）**：每層都是滯後 snapshot → per-prompt 動態注入比 session-start 靜態注入更新鮮
- **Topic 05（先猜再查抗紀律）**：硬紀律改不動 RLHF baseline → hook 注入是「soft」介入（提供內容，不強制使用），符合 agora 證實的可行干預方式
- **Implications 04/05/06**：都收斂在「session-start 加 trigger conditions」方向；M9 是這個方向的動態版本

---

## 路徑依賴

```
M10 (search_memory.py 已完成)
  ↓
M9 Phase A.1 (hook 實作)
  ↓
M9 Phase A.4 (self-pilot 1-2 週)
  ↓
Decision Gate
  ├─ 通過 → A.2 + A.3 (跨 OS 安裝 + 文件)
  ├─ 引用率低 → M10 concept 展開迭代
  ├─ 有負面事件 → M9 設計重評估
  └─ 不穩定 → 延長 pilot 修 bugs
```
