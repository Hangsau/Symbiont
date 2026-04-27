# local-agent — 完整計劃書

> 完整架構分析、缺陷清單、技術挑戰與 Milestones。

---

## 一、現有系統缺陷

| # | 缺陷 | 影響 | 根本原因 |
|---|------|------|---------|
| D1 | evolve 靠 Claude 在 session 裡「回憶」 | context 壓縮後分析失真 | 沒有讀原始 session log |
| D2 | wrap 是手動觸發 | 忘跑 = 不學習 | 沒有自動觸發機制 |
| D3 | memory review_by 只在 /wrap 裡掃描 | 過期記憶靜默留著 | 沒有獨立排程 |
| D4 | rule effectiveness 只記「寫入次數」 | 規則可能沒用但偵測不到 | 缺跨 session 追蹤 |
| D5 | MEMORY.md 102 行，thoughts 50 個 | 快到 200 行上限 | 沒有自動歸檔 |
| D6 | babysit 邏輯全部 Talos 硬編碼 | 無法接第二個 agent | 沒有 agent registry |
| D7 | backup-on-wrap.sh 只在 Skill tool 觸發 | 不是每次 wrap 都備份 | PostToolUse matcher 不夠廣 |

---

## 二、系統架構

```
【Claude 端（本機）】
─────────────────────────────────────────────────────────
人類↔Claude 互動 session（任意專案）
~/.claude/projects/<any-project>/<uuid>.jsonl
    │
    │ Stop hook（session 結束）
    ▼
evolve.py ──→ ~/.claude/CLAUDE.md（全域行為規則）
                   ↑ 反思迴路：只處理人類↔Claude 的互動
                   ✗ 不處理 babysit session（非反思對象）

memory_audit.py ──→ primary_project/memory/ 健康維護
    │ Task Scheduler 每日 02:00

【babysit.py — Agent 協作層】
─────────────────────────────────────────────────────────
Task Scheduler 每 2 分鐘
    │
    ├─ 查 for-claude/<agent>/ 新訊息（Talos 主動）
    │       └─ 有 → claude -p 生引導回應 → outbox
    │
    └─ 查 TEACHING_STATE.md（Claude 主動教學 loop）
            └─ active/waiting_reply → 查 claude-dialogues/ 有回應？
                ├─ 有 → 評估 → 送下一問
                └─ 超時 → 送確認訊息

Transport 抽象（agents.yaml）：
  type: remote_ssh  → SSH/SCP（Talos VM 等遠端 agent）
  type: local       → 本地目錄讀寫（同機 agent）

babysit 產生的 claude -p session 不進 evolve 迴路（設計決定）

【Agent 端（範例：Talos VM）】
─────────────────────────────────────────────────────────
claude-inbox/  ←── babysit.py 送回應
for-claude/    ──→ babysit.py 讀新訊息
claude-dialogues/ ←→ 回應存檔
    │
    └─ dialogue-review / tg-review / weekly-reflection
           └─→ Talos memory（agent 自身反思迴路）
```

**全域 vs primary_project**：

| 概念 | 路徑 | 用途 |
|------|------|------|
| sessions_base | `~/.claude/projects/` | session 掃描（所有專案） |
| primary_project_dir | `~/.claude/projects/{encoded}/` | memory 操作（主專案） |
| global_claude_md | `~/.claude/CLAUDE.md` | 習慣規則讀寫 |

evolve.py 從**所有專案**的 session 萃取習慣 → 寫入**全域** CLAUDE.md。
memory_audit.py 操作的 memory 屬於 primary_project（單一主專案）。

---

## 三、各程式設計

### session_reader.py
```
輸入：~/.claude/projects/**/<uuid>.jsonl（遞迴掃所有專案）
輸出：[{role, content, timestamp}, ...]
邏輯：
  - 只保留 type=user 和 type=assistant 的行
  - 去除 content 裡的 tool_use / tool_result blocks
  - 只保留文字部分
  - 截斷：只取最後 50 條（避免 prompt 過長）
```

### claude_runner.py
```
輸入：prompt_text
輸出：response_text
邏輯：
  subprocess.run(["claude", "-p", prompt_text,
                  "--output-format", "text", "--no-stream"])
  - auth 檢查：確認 ~/.claude/.credentials.json 存在
  - retry 最多 2 次
  - timeout：120 秒
  - 失敗 → 寫 data/error.log，不拋例外
```

### evolve.py
```
流程：
  1. 檢查 data/pending_evolve.txt 是否存在
     → 存在 → 讀取其中的 session uuid，跳到步驟 3
     → 不存在 → 讀 data/state.json，找出最新未處理 session（跨所有專案）
  2. session_reader.py 解析對話
  3. 讀 ~/.claude/CLAUDE.md（全域習慣）+ evolution_log.md canonical topics
  4. 組 prompt → claude_runner.py
  5. 解析 JSON 輸出（rules_to_add, memories_to_update）
  6. 寫入 CLAUDE.md / memory 檔案
  7. Append evolution_log.md
  8. 更新 state.json
  9. 刪除 data/pending_evolve.txt（若存在）
  10. 觸發 backup（robocopy）
觸發機制：
  - Stop hook → 直接 subprocess 背景呼叫 evolve.py（主要路徑）
  - 同時寫 data/pending_evolve.txt（含 session uuid）
  - evolve.py 啟動時先查 pending_evolve.txt，確保不漏跑
  - 若 session 結束後立即關機：下次開機時 Task Scheduler 補跑（pending 檔仍在）
  - Task Scheduler：開機時觸發一次（RunOnce-style，只補漏，非定時）
dry-run 模式：--dry-run 只印出會做什麼，不寫任何檔案
```

### memory_audit.py
```
流程：
  1. Glob memory/*.md，讀 review_by 欄位
  2. 過期條目 → 移至 archive/，從 MEMORY.md 移除
  3. thoughts/ 超過 30 條 → 最舊 10 條移至 archive/thoughts-index.md
  4. MEMORY.md > 170 行 → 輸出警告（不自動刪）
  5. 記錄 data/audit.log
觸發：每天 02:00
dry-run 模式：--dry-run 只印出會做什麼
```

### babysit.py
```
狀態機（Talos 設計）：idle → processing → replied → cooldown(10分) → idle
流程：
  1. 讀 data/agents.yaml（每輪 reload，支援熱更新）
  2. 對每個 agent，掃描訊息目錄新訊息
  3. 訊息來源驗證：skip 自己生成的回覆（見 P8 無限 loop 問題）
  4. 有新訊息 → 讀 data/teaching_state/<agent>.json
  5. 組 prompt（訊息 + 教學狀態 + 保母原則）
  6. claude_runner.py → 若輸出 "NO_REPLY_NEEDED" → skip
  7. 否則寫入 claude-inbox/<agent>/，標記 generated_by metadata
  8. 更新 data/teaching_state/<agent>.json
  9. 建立 data/babysit.lock，結束時刪除
觸發方式：Windows Service（while True + sleep(120)），不用 Task Scheduler
```

---

## 四、預期問題與解決方案

### P1：Task Scheduler 環境找不到 claude CLI

**問題**：Task Scheduler 以不同 session 執行，PATH 可能沒有 claude。

**解法**：
- `run.bat` 明確設定 PATH，包含 claude 安裝路徑
- claude_runner.py 用絕對路徑呼叫（從 config.yaml 讀）
- 啟動前驗證 `claude --version` 可執行，失敗就寫 error log

### P2：evolve.py 和 Claude Code 同時寫 CLAUDE.md

**問題**：session 還開著時 evolve.py 在背景修改檔案。另：兩個 Claude Code session 同時結束 → 兩個 evolve.py 競爭（Talos 發現）。

**解法**：
- evolve.py 用原子寫入（寫 tmp 檔 → `os.replace()` 取代目標，不用 append）
- 寫入前檢查 CLAUDE.md 的 mtime > session 結束時間才處理（避免舊 session 覆蓋新規則）
- file_ops.py 用 file lock（Windows: msvcrt.locking）
- Task Scheduler 設定：Stop 後延遲 10 分鐘觸發

### P3：哪個 session 需要被處理

**問題**：280+ 個 .jsonl，不知道哪個是最新的未處理 session。

**解法**：
- `data/state.json`：`{"last_processed_uuid": "...", "processed_at": "..."}`
- evolve.py 找比 last_processed_uuid 更新（mtime）的 .jsonl
- 每次只處理最新一個

### P4：claude -p prompt 太長

**問題**：長 session + CLAUDE.md + evolution_log 可能超限。

**解法**：
- session_reader.py 只取最後 50 條 turns
- tool_use blocks 壓縮（只保留 tool name，去除完整內容）
- evolution_log 只傳 canonical topics + 最近 14 天條目

### P5：evolve.py JSON 輸出不穩定

**問題**：claude -p 是語言模型，格式可能跑掉。

**解法**：
- prompt 給出明確 JSON schema 和範例
- 解析失敗 → retry 一次（prompt 加「請確保輸出為有效 JSON」）
- 兩次都失敗 → 只記 error.log，不寫任何檔案

### P6：babysit.py 重複執行

**問題**：每 2 分鐘觸發，前一次未結束時下一次又啟動。

**解法**：
- 改為 Windows Service（while True + sleep(120)），天然避免重複啟動
- 若仍用排程：`data/babysit.lock` 存在 → 直接退出；lock 超 10 分鐘 → 強制刪除並 log

---

### P8：babysit.py 無限回覆 loop（Talos 發現）

**問題**：babysit.py 讀到訊息 → 寫回應到 claude-inbox → Talos 回覆到 claude-dialogues → babysit.py 下輪又讀到 → 無限 loop。

**解法**：
- babysit.py 每次寫回應時，在檔名或內容加 metadata：`generated_by: babysit-<timestamp>`
- 輪詢時比對來源：skip `generated_by: babysit-*` 的訊息
- 補充（待 Talos 確認）：Hang 的訊息和 babysit 的回覆走不同目錄，可以從目錄層級做區分，不依賴 metadata

---

### P9：memory_audit.py 邊界情況（Talos 發現）

**問題**：review_by 欄位 null/malformed → 崩潰；歸檔失敗 → 每次都觸發但永遠卡住。

**解法**：
- 每個操作獨立 try-catch，失敗 → log + skip，不影響其他條目
- 歸檔前檢查目標目錄空間與權限
- 歸檔成功才更新計數器，失敗不更新（避免卡在 threshold）

### P7：/wrap SKILL.md 和 evolve.py 職責重疊

**問題**：遷移期間兩者都會跑 evolve，重複寫入。

**解法**：
- M2 完成並驗收後，再修改 wrap SKILL.md 移除步驟 1
- M3 完成並驗收後，再修改 wrap SKILL.md 移除步驟 0
- 遷移前：wrap SKILL.md 不動，evolve.py 加 `--skip-if-wrap-done` 旗標（檢查 .wrap_done.txt）

### P10：Stop hook 和 evolve.py 同時寫入競爭

**問題**：Stop hook 觸發 evolve.py 時，Claude Code session 可能仍在做收尾（寫 .jsonl）。

**解法**：
- Stop hook 先寫 pending_evolve.txt，evolve.py 由 Task Scheduler 在開機時補跑
- 或 Stop hook 延遲 30 秒後才呼叫 evolve.py（subprocess sleep 30 &&）
- session_reader.py 用 mtime 確認 .jsonl 已穩定（mtime > 10 秒前）再讀

---

## 五、Milestones

### M1 — 基礎設施
目標：能讀 session log，能呼叫 claude -p，能追蹤 state

- [x] `utils/session_reader.py`（遞迴掃所有專案）
- [x] `utils/claude_runner.py`（含 auth 檢查、retry、timeout）
- [x] `utils/file_ops.py`（含 file lock）
- [x] `config.yaml`（全域設計：sessions_base + primary_project）
- [x] `data/state.json`（初始化）
- [x] `run.bat` / `run.sh`（Task Scheduler 入口）
- [x] `setup/uninstall_windows.bat` / `setup/uninstall_mac.sh`

驗收：`python src/utils/session_reader.py` 能印出最近 session 的前 5 條對話 ✓

### M2 — evolve.py
目標：session 結束後自動分析並更新規則，關機重開不漏跑

- [ ] `src/evolve.py` 主流程（含 pending_evolve.txt 檢查邏輯）
- [ ] prompt 設計（含 JSON schema）
- [ ] `--dry-run` 模式
- [ ] `--skip-if-wrap-done` 旗標（遷移期間防重複）
- [ ] Stop hook 設定：寫 pending_evolve.txt + 背景呼叫 evolve.py（延遲 30 秒）
- [ ] Task Scheduler 設定：**開機時補跑**（條件：pending_evolve.txt 存在）
- [ ] 修改 wrap SKILL.md：步驟 1 改為「evolve 由 CLI 自動完成，此步驟已遷移」

驗收：
- 正常路徑：session 結束 → 30 秒後 evolution_log.md 有新條目
- 補跑路徑：session 結束後立即關機 → 重開 → evolution_log.md 補跑完成

### M3 — memory_audit.py
目標：每日自動維護記憶系統

- [ ] `src/memory_audit.py` 主流程
- [ ] `--dry-run` 模式
- [ ] Task Scheduler 設定（每天 02:00）
- [ ] 修改 wrap SKILL.md：步驟 0 改為「memory audit 由 CLI 自動完成，此步驟已遷移」

驗收：手動跑 `--dry-run` 確認輸出正確，再跑一次真實執行

### M4 — babysit.py
目標：Talos 保母完全自動化，不依賴 /loop

- [ ] `data/agents.yaml`（Talos 設定）
- [ ] `src/babysit.py` 主流程
- [ ] `data/teaching_state/talos.json`
- [ ] Task Scheduler 設定（每 2 分鐘）
- [ ] 廢除現有 /loop check-talos-reply

驗收：Talos 送訊息 → 確認 babysit.py 在 2 分鐘內自動回應

---

## 六、跨平台支援

Python 程式碼完全共用，差異只在部署腳本：

| 項目 | Windows | Mac |
|------|---------|-----|
| 部署腳本 | `setup/setup_windows.bat` | `setup/setup_mac.sh` |
| 移除腳本 | `setup/uninstall_windows.bat` | `setup/uninstall_mac.sh` |
| 排程 | Task Scheduler（`schtasks`） | launchd（`.plist`） |
| 桌面捷徑 | `run_evolve_now.bat` | `run_evolve_now.sh` |
| Python / claude CLI | 完全一樣 | 完全一樣 |

`setup/` 目錄放四支腳本，新機跑對應的 setup，移除時跑對應的 uninstall。

**uninstall 流程**（兩平台相同邏輯）：
1. 刪 Task Scheduler 任務 / launchd plist
2. 移除 `~/.claude/settings.json` 裡的 Stop hook
3. 刪 `~/.claude/.wrap_done.txt`（若存在）
4. 提示手動刪除 local-agent 資料夾（腳本無法刪自己所在目錄）

---

## 七、不做的事

- reflect（洞見）不移出 /wrap：需要對話的主觀理解
- HANDOFF 不自動化：需要人判斷進度
- 不建 Web UI：純 CLI + 檔案
- 不走 Anthropic API：全部用 claude -p
- 不支援多個 primary_project：memory_audit 只服務一個主專案，多專案 memory 需多個安裝

---

## 八、已知限制

| # | 限制 | 影響 | 說明 |
|---|------|------|------|
| L1 | 結構假設 | 中 | local-agent 必須在 `{workdir}/projects/local-agent/` 以外的安裝位置需手動設 config |
| L2 | wrap skill 配合 | 低 | `--skip-if-wrap-done` 依賴 wrap skill 寫 `~/.claude/.wrap_done.txt`；不配合時旗標永遠不觸發 |
| L3 | 既有 evolution_log | 低 | 已有歷史記錄的用戶需在 config.yaml 設覆蓋路徑，否則從新位置重開 |
