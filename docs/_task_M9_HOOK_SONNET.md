# Sonnet Handoff — M9 Hook Integration Phase A.1

> 從這份檔案複製整段內容貼到新的 Claude Code session（Sonnet）的第一個 prompt。
> 用戶責任：起新 session 時確認模型是 Sonnet，不是 Haiku 或 Opus。

---

## 任務

執行 M9 Hook Integration 的 Phase A.1（hook 實作 + 端到端測試）。Phase A.4（self-pilot）由用戶執行，不在你範圍。Phase A.2/A.3 在 gate 通過後才做，**這次不要碰**。

## 工作目錄

`<Symbiont>/`

## 動手前必讀（依序）

1. `<Symbiont>/CLAUDE.md` — 專案工作守則 + 絕對禁忌
2. `<Symbiont>/HANDOFF.md` — 當前狀態 + 觸發點對照
3. `<Symbiont>/docs/M9_HOOK_INTEGRATION.md` — **本任務的 plan，全部細節在裡面**
4. `<Symbiont>/docs/M10_SEMANTIC_RETRIEVAL.md` — M10 設計（你要呼叫的 search_memory.py 來自這裡）
5. `<Symbiont>/src/search_memory.py` — 你會 import 的 `search()` 函式

## 執行流程

讀完上述文件後，直接跑 `/implement` 把 plan 轉成「步驟 + 對應 skill」實作清單，存成 `Symbiont/.implementation_m9-hook-integration.md`，然後依清單逐步執行。

> M10 也是用同樣模式（看 `.implementation_m10-semantic-memory-retrieval.md` 學格式）

## 範圍：DO

- ✅ 新建 `Symbiont/scripts/run_hook_search.py`（hook entry，pythonw 友好）
- ✅ 更新 `Symbiont/config.yaml` 加 `m9_hook` 區塊（**走 skip-worktree 五步流程**：no-skip → 改本地預設 false → add diff → commit → 改本地實際值 → re-skip）
- ✅ Phase A.1.4 端到端測試矩陣（plan 步驟 4 列出全部 case）
- ✅ 完成後：跑 `/code-audit`、文件對齊掃描、補事件 memory、再 push（HANDOFF 規則）

## 範圍：DO NOT

- ❌ 動 `~/.claude/CLAUDE.md` 或專案 CLAUDE.md（user content，非 Symbiont 範圍）
- ❌ 動 `~/.claude/settings.json`（self-pilot 由用戶執行，你只負責驗證 hook script 本身）
- ❌ 改 `src/search_memory.py` 的演算法（M10 已定型，只能驗證 import 介面）
- ❌ 寫 `setup/install-m9-hook.py`（Phase A.2，gate 通過才做）
- ❌ 改 `Symbiont/CLAUDE.md` 的「各程式職責速查」（Phase A.3，gate 通過才做）
- ❌ 自行決定 Phase A.4 的 baseline 數據怎麼收（用戶執行）

## 絕對禁忌（Symbiont CLAUDE.md 已寫，再強調）

1. **不走 Anthropic API** — search_memory.py 已用 `claude -p` subprocess，你的新程式必須維持
2. **exception 一律 exit 0** — run_hook_search.py 任何錯誤都不能往外拋，會擋 user session
3. **不用 shell=True** — subprocess 一律 list args；prompt 內容絕不組進 shell command
4. **派 sub-agent 修改檔案的紀律**（套 `subagent-git-no-mutation` skill）：prompt 必須明確禁 `git reset / pull --rebase / stash / checkout -- / clean`；每完成一條任務立刻 commit（即使 WIP）

## 完成標準

Phase A.1 通過 = 以下全部成立：
- `run_hook_search.py` 完成且通過 plan 列出的測試矩陣（空 / 短 / 巨長 10K+ / 特殊字元 / null bytes / 表情 / 並發 5 / search 故意 raise）
- `config.yaml` 的 `m9_hook` 區塊已 commit（skip-worktree 流程乾淨，發行版預設 `enabled: false`）
- `/code-audit` 通過 + 文件對齊掃描通過（`docs/M9_HOOK_INTEGRATION.md` 是 source of truth，不要新建重複文件）
- `HANDOFF.md` 更新一行 M9 狀態：「Phase A.1 完成，等待用戶 self-pilot」
- 最後一個 commit + push

## 完成後給用戶的回報

精簡到三件事：
1. 哪些檔案動了（含 commit hash）
2. 測試矩陣結果
3. 用戶 self-pilot 時要怎麼啟用（手動改 settings.json 的具體片段 + 改 config.yaml `enabled: true` 的指令）

不要追加 Phase A.2 / A.3 / B / C 的建議 — 那是 gate 通過後的決策。

## 工具/模型路由提醒

- 派 Agent 做純 Read/Grep/檢查 → `model: "haiku"`
- 派 Agent 寫程式 / 設計 → 不指定（Sonnet）
- 你自己（執行者）就是 Sonnet，主邏輯自己做

## 卡住怎麼辦

- Plan 不夠清楚 → Read plan 第二遍；還不夠就 stop 問用戶（不要憑感覺繼續）
- 測試 case fail → 修 hook script，不要改 plan 降低標準
- 跟 M10 既有程式衝突 → stop 問用戶（M10 行為不能改）

開工。
