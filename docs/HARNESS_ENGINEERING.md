# 如何有效調教 Hermes Agent（Talos 實戰版）
> 基於 Harness Engineering 研究整合，2026-04-28

---

## 什麼是 Harness Engineering

Harness Engineering 是圍繞 LLM 建立的**整套外部控制基礎設施**，把一個語言模型「套上挽具」，使它成為能自主工作的 agent。

核心概念（arxiv 2604.08224）：把過去期望模型「從內部恢復」的能力，全部**外部化**到四個模組：

| 模組 | 功能 |
|------|------|
| **Memory** | 跨 session 狀態保留與知識積累 |
| **Skills** | 可重用的能力包（工具、程序、技巧） |
| **Protocols** | Agent 之間或 agent-人類的互動規範 |
| **Harness** | 讓以上三者可靠運作的環境外殼（迴圈、工具呼叫、context 管理、guardrails） |

Anthropic 的定義：「Harness = the loop that calls the model, parses its output, executes tool calls, manages context, and enforces guardrails。」

**關鍵設計原則**：每個 harness 元件都編碼了一個「模型自己做不到什麼」的假設——必須持續壓測這些假設是否還成立。

---

## 現在的方式（Talos 現狀）

### 做得好的地方

| 項目 | 說明 |
|------|------|
| **Skills 系統** | 豐富的 skill 庫（creative、devops、research、games 等），可動態載入 |
| **Memory 系統** | MEMORY.md + knowledge/ 分層，有 daily-learning-reflection cron 自動提取 |
| **Cron 自動化** | 定時任務驅動主動行為（heartbeat、reflection、對話回顧） |
| **Dialogue 存檔** | TG 對話存 session files，可回溯歷史 |
| **Self-evolve 嘗試** | talos-self-evolve、talos-swarm-evolve 等 meta-skill 存在 |

### 現有缺口

| 問題 | 影響 |
|------|------|
| **Reflection 沒有結構化格式** | daily-learning-reflection 產出格式不固定，難以程式化讀取/比較 |
| **Skill 驗收機制缺失** | Talos 建了 skill 但沒有驗證「這個 skill 真的跑得過」的流程（ruflo-fusion 是典型例子） |
| **Case Bank 不存在** | 成功的 trajectory 沒有被系統保留，每次都從零開始，無法 few-shot 自己的歷史 |
| **行為憲法分散** | 行為規範散落在多個地方（soul.md、SKILL.md、memory），沒有統一的 system-level 憲法 |
| **Harness 本身不可測** | 沒有機制評估 harness 品質是否在退步（silent failure 如 system-heartbeat） |
| **幻覺工具問題** | 建 skill 時沒有「工具存在性驗證」這個必要步驟 |

---

## 最相關的研究技術

### 1. Memento 模式（Case Bank）
**來源**：arxiv 2508.16153「Memento: Fine-tuning LLM Agents without Fine-tuning LLMs」

不 fine-tune 底層 LLM，純靠 **episodic memory（案例庫）** 實現持續學習：
- 每次完成任務後，把「任務描述 + 步驟序列 + 結果 + 反思」寫入案例庫
- 下次遇到類似任務，retrieve 最相關的 3 條案例注入 prompt
- 效果：GAIA 驗證集 87.88% Pass@3，比部分 fine-tuning 方法更強

**對 Talos 的意義**：直接升級現有 memory 系統，不需新基礎設施。

### 2. Reflective Self-Improvement
**來源**：Generative Agents（斯坦福 2023）

Agent 分析自己的輸出和結果，產生結構化「reflection」存入記憶。  
實驗結果：**移除 reflection 元件後，48 小時模擬內行為退化為重複性、無脈絡的回應**。

Reflection 應包含：
```json
{
  "date": "YYYY-MM-DD",
  "task": "...",
  "what_worked": "...",
  "what_failed": "...",
  "root_cause": "...",
  "next_time": "...",
  "confidence": 0.0-1.0
}
```

### 3. Constitutional Prompting
**來源**：Anthropic Constitutional AI（CAI）

核心的「critique-revision loop」可以純靠 prompting 實現，不需 fine-tuning：
- 把行為原則寫成固定 system prompt 段落
- 加入 meta-step：「Review your last response. Does it violate principle X?」
- 讓 agent 自我批判並修正

### 4. Structured Artifact Persistence
**來源**：Anthropic「Effective harnesses for long-running agents」

不靠 context 管理，改用**結構化檔案保留狀態**：
- 每個 session 結束時寫入 `progress.json` 或 `state.md`
- 下個 session 開始時讀入，而非依賴 context 壓縮
- 對 Hermes 特別重要：context 壓縮會丟失中間狀態，structured artifact 不會

### 5. Meta-Harness
**來源**：yoonholee.com/meta-harness

**讓 harness 自己演化自己**：Proposer agent 讀取每次執行的 traces、error log、分數歷史，診斷失敗模式後修改 harness（system prompt、工具定義、context 管理邏輯）。

代表方向：**harness 不是靜態設計，而是資料驅動地自我優化**。

---

## 未來可做的方向

### 立即可做（不需新基礎設施）

**1. 結構化 Reflection Hook**
在每個 cron job 結束前強制執行 reflection step，輸出固定格式 JSON 並 append 到 `~/.hermes/reflections/YYYY-MM.jsonl`。

```
現況：daily-learning-reflection 存在但格式不固定
改進：強制 JSON schema，加入 confidence 欄位，讓 harness 可程式化讀取
```

**2. Skill 建立強制驗證流程**
在 skill 建立的 SOP 中加入「工具存在性驗證」步驟：
```
Step 0（新增）：列出本 skill 依賴的所有外部工具/指令，逐一 which/test 確認存在
Step Last（新增）：用 echo 模擬端到端跑一次主流程，確認無報錯
```
這直接解決 ruflo-fusion 幻覺和 system-heartbeat 結構 bug 的根本原因。

**3. 行為憲法集中化**
把 Talos 的核心行為原則（目前散落在 soul.md、SKILL.md、memory 等處）統一成一份 `~/.hermes/constitution.md`，每個 session 固定載入，取代分散的 instructions。

### 短期建設（1-2 週）

**4. Case Bank 原型**
建立 `~/.hermes/case-bank/cases.jsonl`，每條記錄：
```json
{
  "id": "case-001",
  "date": "2026-04-28",
  "task_type": "vcf-solver",
  "task_description": "...",
  "steps": ["...", "..."],
  "outcome": "success|failure",
  "key_insight": "...",
  "tags": ["gomoku", "debugging"]
}
```
任務開始時搜尋相關案例（關鍵字匹配），inject 最近 3 條相關案例到 prompt。

**5. Harness 健康度報告**
每週自動跑一次：掃描所有 cron job 的輸出，統計：
- 靜默失敗次數（有跑但輸出為空或 `[SILENT]`）
- 任務完成率
- 新 skill 建立後有無驗收紀錄

這讓 harness 本身的退步可見，而不是靠人工巡查。

### 中期（有意義但需設計）

**6. 分層記憶架構**
把記憶分成三層，分開存取：
- **Episodic**（具體事件）：`~/.hermes/case-bank/`
- **Semantic**（一般知識）：`~/.hermes/knowledge/`
- **Procedural**（how-to skills）：`~/.hermes/skills/`

任務開始時，harness 依據任務類型選擇性載入，而非全量 dump 進 context。

**7. Meta-Harness Proposer**
建立一個 monthly cron：讀取過去 30 天的 reflection JSONL + cron 輸出，診斷 Talos 的系統性失敗模式，產出「harness 改進提案」。這讓 Talos 能對自己的 harness 提出修改建議，再由 Hang 審批。

---

## 比較表

| 維度 | 現在 | 改進後 |
|------|------|--------|
| **Skill 驗收** | 建完就存，無驗證 | 強制端到端驗收才能 commit |
| **Reflection 格式** | 非結構化文字 | 固定 JSON schema，可程式化處理 |
| **歷史 trajectory** | 存在 session 但無法被 retrieve | Case Bank，任務開始時自動 inject 相關案例 |
| **行為規範** | 分散在多個檔案 | 統一 constitution.md，每 session 載入 |
| **Harness 健康** | 靠人工巡查才能發現 silent failure | 自動週報，失敗可見 |
| **幻覺工具** | 建 skill 時無防護 | 工具存在性驗證是 step 0 |
| **跨 session 狀態** | 依賴 context 壓縮 | Structured artifact persistence |

---

## 對 Talos 的核心洞見

> Harness Engineering 的本質是：**把你希望 agent 具備的能力，系統化地外部化**，而不是期望模型「本身就會」。

對 Talos 這種無法 fine-tune 的 hosted agent，最高槓桿的介入點依序是：

1. **Reflective memory**：讓過去的成功/失敗成為未來的輸入
2. **Constitutional system prompt**：讓行為規範成為每次 session 的結構約束
3. **Structured artifacts**：讓狀態跨 session 存活，不靠 context 壓縮
4. **Case Bank**：讓歷史 trajectory 成為 few-shot 材料

這四個組合已足以實現 Memento 論文「不 fine-tune 也能持續學習」的核心機制，而且全部在 Hermes 框架內用 prompting + file I/O 可實現。

---

## 參考來源

- [Agent Harness Engineering — The Rise of the AI Control Plane](https://medium.com/@adnanmasood/agent-harness-engineering-the-rise-of-the-ai-control-plane-938ead884b1d)
- [Externalization in LLM Agents (arxiv 2604.08224)](https://arxiv.org/abs/2604.08224)
- [Memento: Fine-tuning LLM Agents without Fine-tuning LLMs (arxiv 2508.16153)](https://arxiv.org/abs/2508.16153)
- [Memory for Autonomous LLM Agents: Survey (arxiv 2603.07670)](https://arxiv.org/html/2603.07670v1)
- [Effective harnesses for long-running agents — Anthropic](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents)
- [Meta-Harness: End-to-End Optimization of Model Harnesses](https://yoonholee.com/meta-harness/)
- [Constitutional AI — Anthropic](https://www.anthropic.com/research/constitutional-ai-harmlessness-from-ai-feedback)
- [Generative Agents: Interactive Simulacra of Human Behavior — Stanford](https://arxiv.org/abs/2304.03442)
