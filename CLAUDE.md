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
├── HANDOFF.md          # 當前進度
├── PLAN.md             # 完整計劃書（架構、缺陷分析、milestones）
├── src/
│   ├── evolve.py           # session 分析 → 規則寫入 CLAUDE.md
│   ├── memory_audit.py     # review_by / archive / 容量管理
│   ├── babysit.py          # agent 保母：poll inbox → 決策 → 回應
│   └── utils/
│       ├── session_reader.py   # 解析 .jsonl session log
│       ├── claude_runner.py    # claude -p subprocess 封裝
│       └── file_ops.py         # 安全讀寫（含 file lock）
├── data/
│   ├── state.json          # 記錄最後處理的 session uuid
│   └── agents.yaml         # agent registry（gitignore，從 agents.example.yaml 複製）
├── config.yaml             # 路徑、閾值設定
└── run.bat                 # Windows Task Scheduler 入口
```

---

## 各程式職責速查

| 程式 | 輸入 | 輸出 | 排程 |
|------|------|------|------|
| `evolve.py` | 最新 .jsonl session log | CLAUDE.md 規則更新、evolution_log append | Stop hook 直接觸發（30秒延遲）；開機補跑（pending_evolve.txt 存在時） |
| `memory_audit.py` | memory/*.md 的 review_by 欄位 | archive 移動、MEMORY.md 更新 | 每天 02:00（Task Scheduler，開機補跑） |
| `babysit.py` | for-claude/<agent>/ 新訊息 | claude-inbox/<agent>/ 回應 | 每 2 分鐘（Windows Service） |

---

## 絕對禁忌

- **evolve.py 輸出格式解析失敗時，不能寫任何檔案**：fallback 只記 error log，寧可不執行也不亂寫
- **babysit.py 不能直接替 agent 完成任務**：只發問、引導、提示；代做等於廢掉教學機制
- **memory_audit.py 不能刪 evolution_log.md**：append-only，永遠不碰
- **不走 Anthropic API**：所有 LLM 呼叫必須用 `claude -p` subprocess

---

## 環境需求

- Python 3.10+
- Claude Code CLI 已安裝並登入（`claude` 在 PATH 中）
- Windows Task Scheduler（排程觸發）
- 測試時可手動呼叫：`python src/evolve.py --dry-run`

---

## 與現有系統的邊界

| 功能 | 舊（SKILL.md） | 新（Symbiont） | 遷移時機 |
|------|--------------|-----------------|---------|
| evolve 規則分析 | wrap 步驟 1 | evolve.py | M2 完成後 |
| memory audit | wrap 步驟 0 | memory_audit.py | M3 完成後 |
| reflect 洞見 | wrap 步驟 2 | **保留在 wrap**（需對話理解） | 永不遷移 |
| HANDOFF 提示 | wrap 步驟 3 | **保留在 wrap** | 永不遷移 |
| agent 保母 | 手動觸發 | babysit.py | M4 完成後 |
