# local-agent — 交接單

## 當前狀態

**階段**：M4 實作完成 + code review，待驗收（2026-04-27）

---

## 已完成

### M1 — 基礎設施（全域設計）
- `config.yaml`：`primary_project`、sessions 全域掃描、`wrap_done_file` 固定路徑
- `src/utils/config_loader.py`：三層路徑優先序（env var > config > 自動偵測）
- `src/utils/session_reader.py`：`rglob("*.jsonl")` 遞迴掃所有專案
- `setup/uninstall_windows.bat` + `uninstall_mac.sh`

### M2 — evolve.py
- pending_evolve.txt 優先 → state.json fallback
- JSON 失敗**只記 error.log，不寫任何檔**（絕對禁忌）
- CLAUDE.md 原子寫入（FileLock）、evolution_log append
- `--dry-run`、`--skip-if-wrap-done` 旗標

### M3 — memory_audit.py + 基礎設施
- `src/memory_audit.py`：valid_until 歸檔、review_by 報告、thoughts 溢出歸檔、容量警告
- `~/.claude/scripts/local-agent-stop-hook.sh`：Stop hook（session_id → pending 旗標 → evolve.py 背景啟動）
- `setup/setup_windows.bat`：動態路徑（`%~dp0`）、pip install、Task Scheduler、Stop hook 注入
- `setup/setup_memory.bat` / `setup_memory.sh`：memory/ 骨架初始化
- `docs/COMMANDS.md`：Claude 操作手冊（安裝/啟用/移除全部透過告訴 Claude 執行）
- `docs/MEMORY_SCHEMA.md`：memory 格式規範模板
- `requirements.txt`：PyYAML>=6.0
- `config.yaml`：加 `memory_audit.enabled/auto_archive`、`pending_audit` 路徑
- `~/.claude/settings.json`：Stop hook 加入 local-agent-stop-hook.sh

### M3 驗收結果
- Stop hook 端到端：session_id 正確寫入 pending 旗標 ✓
- dry-run：偵測 3 個 valid_until + 10 個 thoughts 待歸檔 ✓
- enabled=false 時靜默跳過 ✓

### evolve.py 首次真實執行（2026-04-27）
Stop hook 觸發後發現 3 個 Windows 特有問題，已修復：

| 問題 | 修復位置 |
|------|---------|
| 背景 bash 缺 npm PATH → claude not found | `local-agent-stop-hook.sh` 加 PATH export（含 nvm 支援） |
| `--no-stream` flag 不存在 | `claude_runner.py` 移除 |
| `.cmd` 不是原生執行檔，subprocess 失敗 | `claude_runner.py` `_resolve_cmd()` 改用 `node + cli.js` |

**跨平台補強（2026-04-27）**：Mac/Linux 上 nvm/Homebrew 路徑在 hook 環境同樣可能缺失。
- `_resolve_cmd()` 新增 Mac/Linux 自動偵測（shutil.which → 掃常見路徑 → nvm 最新版）
- `local-agent-stop-hook.sh` 補 Homebrew + nvm 動態 PATH

首跑結果：session 8cdf14fc（50 turns）→ 萃取 2 條規則 → 寫入 CLAUDE.md ✓

---

### M4 — babysit.py（2026-04-27 實作完成，待驗收）

#### 架構設計決策（本 session 確認）

| 決策 | 結論 |
|------|------|
| babysit session 進反思迴路？ | 否。`claude -p` 建的 session evolve.py 自然掃到，但反思是針對人類互動；agent 互動不作為反思對象 |
| babysit vs teach 分兩支？ | 否。統一在 babysit.py：inbox 訊息（agent 主動）+ teaching loop（Claude 主動）合一 |
| Transport 抽象 | agents.yaml `type: remote_ssh`（VM/SSH）或 `type: local`（同機目錄），零程式碼改動支援任意 agent |
| 系統定位 | 模組化生態：evolve / memory_audit / babysit 各自獨立啟用，互不依賴 |

#### 新增 / 修改檔案

| 檔案 | 說明 |
|------|------|
| `data/agents.yaml` | Agent registry（Talos 設定 + local 範例） |
| `src/babysit.py` | 完整實作（SSHTransport + LocalTransport + lock + teaching loop + code review 完成） |
| `data/teaching_state/` | Per-agent 教學狀態目錄 |
| `setup/setup_windows.bat` | 加入 local-agent-babysit Task Scheduler（每 2 分鐘） |
| `docs/COMMANDS.md` | 加入：啟用 babysit / 停用 / 教學 loop / 設定新 agent / 換機遷移 |
| `PLAN.md` | 架構圖更新（含反思迴路設計決策） |

#### Code Review 修復（13 項）

| 類型 | 項目 |
|------|------|
| 無效 import | `get_path` 移除 |
| Import 位置 | `import tempfile, os` 移至模組頂層 |
| 魔法數字 | 8 個提取為具名常數（`SSH_CONNECT_TIMEOUT`、`SSH_TIMEOUT_SECONDS`、`SCP_TIMEOUT_SECONDS`、`DIALOGUES_FETCH_COUNT`、`DRY_RUN_PREVIEW_CHARS`、`MAX_PROCESSED_INBOX_HISTORY`、`TEACHING_TIMEOUT_SECONDS`、`LAST_QUESTION_MAX_CHARS`） |
| dry_run 不一致 | `send_reply` 移除 `dry_run` 參數；呼叫端用 `if not dry_run:` 包裝 |
| double time.time() | confirm_msg 先算 `ts` 再組字串 |
| 死碼 | `MAX_ROUNDS_REACHED` 分支移除（pre-check 已處理，prompt 指令也一併移除） |

廢除：`/loop check-talos-reply` 不再需要手動執行（babysit.py 自動接管）

---

## 待完成

### 立即可做（換機前）
1. **安裝 Task Scheduler**（本機尚未跑 setup）：
   ```
   cd C:/claudehome/projects/local-agent
   setup/setup_windows.bat
   ```
   驗證：`schtasks /Query /TN "local-agent-babysit"`

2. **啟用 memory audit**（本機尚未啟用）：
   告訴 Claude「幫我啟用 local-agent 的 memory 系統」

3. **M4 驗收**：Talos 送 for-claude/ 訊息 → 確認 babysit 2 分鐘內回應

### 換機遷移
見 `docs/COMMANDS.md` → 「換機遷移 local-agent」章節。
關鍵：SSH key 需複製、`claude auth login`、跑 `setup_windows.bat`。

---

## 設計決策（已定案）

| 概念 | 路徑 | 用途 |
|------|------|------|
| sessions_dir | `~/.claude/projects/` | session 全域掃描 |
| primary_project_dir | `~/.claude/projects/{encoded}/` | memory 操作（主專案） |
| global_claude_md | `~/.claude/CLAUDE.md` | 習慣規則讀寫 |
| wrap_done_file | `~/.claude/.wrap_done.txt` | wrap ↔ evolve.py 協調 |
| evolution_log | `C:/claudehome/projects/evolve/evolution_log.md`（config 覆蓋） | 進化記錄 |
| pending_evolve | `data/pending_evolve.txt` | Stop hook → evolve 旗標（含 session_id） |
| pending_audit | `data/pending_audit.txt` | Stop hook → memory_audit 旗標 |
| agents_registry | `data/agents.yaml` | babysit.py agent 設定（每輪 reload） |
| teaching_state | `data/teaching_state/<agent>.json` | per-agent 教學進度 |

## 觸發機制

| 程式 | 觸發 |
|------|------|
| `evolve.py` | Stop hook（30s 延遲）+ 開機補跑（pending 存在時） |
| `memory_audit.py` | Stop hook（同步寫旗標）+ 開機補跑（pending 存在時） |
| `babysit.py` | Task Scheduler 每 2 分鐘（需電腦開著） |

## 使用者控制

操作全透過 `docs/COMMANDS.md` + 告訴 Claude，不需手動改 config 或跑腳本。

## 注意事項

- `local-agent-stop-hook.sh` 需要 `LOCAL_AGENT_DIR` 環境變數（settings.json 已設定為 `C:/claudehome/projects/local-agent`）；搬移 repo 時要更新
- evolve.py JSON 失敗 → 只記 error.log，不寫任何檔（絕對禁忌）
- memory_audit.py `enabled: false`（發行預設）→ 需使用者主動啟用
- evolution_log 設定為 `C:/claudehome/projects/evolve/evolution_log.md` 保留歷史連續性
- babysit.py 不分析自己的 session（agent 互動不是反思對象）
