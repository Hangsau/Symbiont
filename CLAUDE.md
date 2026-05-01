# Symbiont — 工作守則

## 專案定義

Claude Code 的本地 Python Agent，讓 Claude 的記憶管理、行為進化、agent 保母邏輯脫離 session 束縛，改由排程自動執行。

**核心原則：Claude Code session 只是輸入資料，不再是執行容器。**

計費模式：用 `claude -p` subprocess（subscription），不走 Anthropic API（per-token）。

---

## 目錄結構

```
Symbiont/
├── CLAUDE.md           # 本文件
├── src/
│   ├── evolve.py           # session 分析 → 規則寫入 CLAUDE.md
│   ├── synthesize.py       # 跨 session 批次分析 → skill 生成 / memory 蒸餾
│   ├── memory_audit.py     # review_by / archive / 容量管理
│   ├── babysit.py          # agent 保母：poll inbox → 決策 → 回應（teaching/discussion 雙 mode）
│   ├── healthz.py          # CLI：讀 heartbeat.json 回報 babysit 健康狀態
│   └── utils/
│       ├── session_reader.py      # 解析 .jsonl session log
│       ├── friction_extractor.py  # 糾正信號提取（Guard skill 原料）
│       ├── habit_extractor.py     # 習慣模式提取（Workflow/Audit skill 原料）
│       ├── turn_utils.py          # 共用：extract_context()
│       ├── knowledge_writer.py    # knowledge/ 寫入、KNOWLEDGE_TAGS.md 維護、distilled 搬移
│       ├── claude_runner.py       # claude -p subprocess 封裝
│       ├── config_loader.py       # 路徑/設定載入（auto-detect warning）
│       ├── transport.py           # SSH/Local agent transport（含 _quote_remote_path）
│       └── file_ops.py            # 安全讀寫 + FileLock（O_CREAT | O_EXCL）
├── tests/                  # pytest 整合測試（102 條，含並發 / 階段化 / quoting）
├── data/
│   ├── state.json          # evolve cursor v2（last_processed_mtime + processed_recent[50]）
│   ├── synth_state.json    # synthesis cursor v2 + staged commit 欄位（gitignore）
│   ├── memory.lock         # 自動建立：memory_audit / synthesize 共用鎖
│   ├── babysit.lock        # 自動建立：babysit 並發保護
│   ├── heartbeat.json      # babysit 每次執行寫入：last_run_ts + agents_pinged（healthz 讀）
│   └── agents.yaml         # agent registry（gitignore，從 agents.example.yaml 複製）
├── scripts/
│   ├── trigger-evolve.py      # Stop hook：寫三個 pending .txt 旗標，純檔案操作
│   ├── run_evolve.py          # Task Scheduler wrapper：每 1 分鐘 poll pending_evolve.txt，有才跑 evolve.py
│   ├── run_audit.py           # Task Scheduler wrapper：每日 04:00 無條件跑 memory_audit.py
│   ├── run_babysit.py         # Task Scheduler wrapper：每 2 分鐘執行 babysit.py
│   └── symbiont-stop-hook.sh  # Stop hook 腳本（Mac/Linux 安裝用）
├── vm-bootstrap/
│   ├── SETUP.md               # claude -p 執行用的安裝指令集（給 VM 端 Claude 讀）
│   ├── secrets.example.env    # 憑證模板（複製為 ~/secrets.env 並填入真實值）
│   └── run.sh                 # 啟動腳本（驗證 credentials + 呼叫 claude -p）
├── config.yaml             # 路徑、閾值設定
└── run.bat                 # Windows Task Scheduler 入口
```

---

## 各程式職責速查

| 程式 | 輸入 | 輸出 | 排程 |
|------|------|------|------|
| `evolve.py` | 最新 .jsonl session log | CLAUDE.md 規則更新、evolution_log append | Stop hook 寫 pending_evolve.txt → `scripts/run_evolve.py`（pythonw.exe）每 1 分鐘 poll；每 10 次後觸發 synthesize.py |
| `synthesize.py` | 最近 N 個 session 的 friction + habit 片段 + 現有 skill descriptions | `~/.claude/skills/` 新建或迭代 skill（quality_score < 2 跳過）、memory/thoughts/ 洞見、knowledge/<type>/ 蒸餾知識、低使用率 skill 清掃 | 由 evolve.py 計數觸發（每 10 次 session） |
| `memory_audit.py` | memory/*.md 的 review_by 欄位 | archive 移動、MEMORY.md 更新 | 每日 04:00（`scripts/run_audit.py`，pythonw.exe，無條件執行） |
| `babysit.py` | for-claude/<agent>/ 新訊息 | claude-inbox/<agent>/ 回應；data/heartbeat.json | 每 2 分鐘（`scripts/run_babysit.py`，pythonw.exe）。LLM 第一行輸出 `MODE: teaching\|discussion` 標籤決定後續對話模式 |
| `healthz.py` | data/heartbeat.json | stdout 健康報告 + exit code 0/1 | 手動 CLI（`python src/healthz.py [--max-age N] [--allow-partial] [--json]`） |

---

## 保姆行為規範

babysit.py 代表 Claude Code 回應 agent 訊息時，必須遵守共生計劃教學框架：
- 完整原則見 `docs/SYMBIOSIS_TEACHING_GUIDE.md`
- 核心：引導而非代做、Loud/Silent failure 介入判斷、結構化 Reflection、Skill 生命週期驗收

**Mode 切換（dual-mode conversation）**：
- LLM 在 inbox 第一輪回應的第一行輸出 `MODE: teaching` 或 `MODE: discussion`，固化進 `TeachingState.mode`
- teaching 模式：蘇格拉底引導，達成輸出 `GOAL_ACHIEVED` 結束
- discussion 模式：平等對話，話題自然結束輸出 `NO_REPLY_NEEDED` 結束
- 標籤未輸出 / 不認識時 fallback `teaching`（保守選擇）

---

## 知識查找順序

需要查找背景資訊或不認識的名詞時，依序執行：

1. `Grep knowledge/KNOWLEDGE_TAGS.md <關鍵字>` → 找到 → Read `knowledge/<type>/<file>.md`
2. 找不到 → `Grep memory/ <關鍵字>`（尚未蒸餾的新記憶）
3. 都找不到 → 問用戶

**操作說明**：`docs/COMMANDS.md` 含完整操作指引（synthesize 手動觸發、knowledge base 查詢、索引重建）

---

## 絕對禁忌

- **evolve.py / synthesize.py 輸出格式解析失敗時，不能寫任何檔案**：fallback 只記 error log，寧可不執行也不亂寫
- **babysit.py 不能直接替 agent 完成任務**：只發問、引導、提示；代做等於廢掉教學機制
- **memory_audit.py 不能刪 evolution_log.md**：append-only，永遠不碰
- **不走 Anthropic API**：所有 LLM 呼叫必須用 `claude -p` subprocess

---

## 環境需求

- Python 3.10+
- Claude Code CLI 已安裝並登入（`claude` 在 PATH 中）
- Windows Task Scheduler（排程觸發）
- 測試時可手動呼叫：`python src/evolve.py --dry-run` / `python src/synthesize.py --dry-run`

---

## 與現有系統的邊界

| 功能 | 舊（SKILL.md） | 新（Symbiont） | 遷移時機 |
|------|--------------|-----------------|---------|
| evolve 規則分析 | wrap 步驟 1 | evolve.py | M2 完成後 |
| memory audit | wrap 步驟 0 | memory_audit.py | M3 完成後 |
| reflect 洞見 | wrap 步驟 2 | **保留在 wrap**（需對話理解） | 永不遷移 |
| HANDOFF 提示 | wrap 步驟 3 | **保留在 wrap** | 永不遷移 |
| agent 保母 | 手動觸發 | babysit.py | M4 完成後 |
| 跨 session skill 生成 | 無 | synthesize.py | M7 完成後 ✅ |
| memory 長期知識庫 | 無 | synthesize.py + knowledge_writer.py | M8 完成後 ✅ |
