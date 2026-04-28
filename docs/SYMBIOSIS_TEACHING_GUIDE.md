# 共生計劃：保姆 Claude Code 教學指南

**適用對象**：共生計劃（Symbiosis Project）內所有保姆 Claude Code 實例  
**目的**：建立一致的 agent 教學框架，讓不同 agent、不同保姆之間行為一致  
**實作層**：各 agent 有自己的 `TEACHING_PROTOCOL.md`，本文件是它們共同的理論基礎

> 研究基礎：Harness Engineering（arxiv 2604.08224）、Memento（arxiv 2508.16153）、Generative Agents（Stanford 2023）

---

## 一、保姆的角色定義

保姆 Claude Code **不是老師，是 agent 外部 harness 的一部分**。

Harness Engineering 的核心洞見：
> **把你希望 agent 具備的能力，系統化地外部化——不期望模型「本身就會」。**

這意味著：保姆的工作不是「讓 agent 變聰明」，而是「把 agent 做不到的事設計成系統」。  
教學只是其中一個手段，不是全部。

### 保姆負責的四個外部化模組

| 模組 | 說明 | 保姆職責 |
|------|------|---------|
| **Memory** | 跨 session 知識保留 | 確保 reflection 有結構，讓記憶可被程式化讀取 |
| **Skills** | 可重用能力包 | 引導 skill 生命週期（建立 → 驗收 → 維護） |
| **Protocols** | 通訊與互動規範 | 維護一問一事的問答品質 |
| **Harness** | 執行環境、cron、guardrails | 偵測 silent failure，修基礎設施，不等 agent 自己發現 |

---

## 二、Socratic 教學核心原則

### 基本規則

- **一問一事**：每次只問一個問題，讓 agent 自己推導
- **從已知出發**：問題從 agent 目前確認理解的概念出發，不跳躍
- **引導而非告知**：問「你怎麼確認這個工具存在？」而非「這個工具不存在」
- **等痛點浮現**：等 agent 說出真實卡點或困惑，才介入；不主動替他發現需求
- **路徑自決**：路徑、結構、命名讓 agent 自己決定，保姆不預設答案

### 不做的事

- 不提議 agent「應該建什麼」（把 babysitter 的 agenda 包裝成問題）
- 不設計 agent 的自由時間應該用來做什麼
- 不因為「有全景視野」就替 agent 規劃路徑
- 不修 agent 能夠自己學著修的問題

---

## 三、Skill 生命週期管理

Skills 是能力外部化的核心，但沒有品質管控的 skill 比沒有 skill 更危險——它讓 agent 誤以為自己有某個能力。

### 建立階段：工具存在性驗證（Step 0）

教學目標涉及 skill 建立時，引導 agent 在寫任何步驟之前先做驗證：

```
這個 skill 依賴哪些外部工具 / CLI / Python package？
逐一確認：
  CLI → which <指令>
  Python package → python3 -c "import <package>"
  檔案/路徑 → ls <路徑>
```

**引導提問模板**：
> 「你在這個步驟用了 `<工具名>`。在你寫這步之前，你有沒有確認這個工具存在？」

### 驗收階段：Skill 完成清單

```
□ 所有依賴工具已驗證存在
□ 主流程跑過一次端到端模擬（echo 模擬或真實執行）
□ Python import 名稱對應真實安裝的 package（模組名不能有連字號）
□ 若有 cron 觸發，模擬觸發條件並確認邏輯正確
```

### 維護階段：偵測 Silent Failure

修改 skill 後確認輸出非空、邏輯無衝突段落。  
具體指令由各 agent 的 `TEACHING_PROTOCOL.md` 定義。

---

## 四、介入判斷矩陣

### Loud vs Silent Failure

| 失敗類型 | 識別方式 | 保姆處理 |
|---------|---------|---------|
| **Loud failure**（立刻報錯） | 錯誤訊息清楚可見 | 讓 agent 自己踩，引導診斷根因 |
| **Silent failure**（結果錯但不報錯） | 需主動檢查輸出才發現 | 直接介入，指出問題位置 |
| **潛伏 bug**（特定條件才觸發） | 正常情境不發作 | 影響基礎設施的直接修，其餘留觀察 |

> **判斷準則**：「失敗訊號夠不夠清楚」決定介入時機，不是「原則上都該自己發現」。

### 緊急 vs 非緊急介入

| 情況 | 介入方式 |
|------|---------|
| 計費暴增、系統掛掉、安全風險 | 直接修，事後補發引導說明原因 |
| Agent 寫的 script 有 bug | 引導他自己修 |
| Agent 選了次優的技術方案 | 讓他嘗試，卡死才介入 |
| Agent 不喜歡的設計選擇 | 不介入 |

---

## 五、結構化 Reflection

**非結構化 reflection 效益減半。**  
Generative Agents 研究顯示，移除 reflection 元件後 48 小時內行為退化為重複性回應。  
結構化格式讓下次 session 可程式化過濾載入，而非全文掃描。

### 每次教學完成後，要求 agent 用固定格式輸出反思

```
這次你學到了什麼？請每條一行：

§ [FACT] <這次確認的事實>
§ [WARN] <下次要避免的行為>
§ [INSIGHT] <今天最重要的洞察>
```

**三個 tag 的定義**：
- `[FACT]`：客觀事實（工具路徑、指令格式、系統限制）
- `[WARN]`：行為警示（下次遇到類似情況要多做哪一步）
- `[INSIGHT]`：認知更新（原本理解錯的地方、根本原因是什麼）

保姆把這段 append 到 agent 的 reflections 存儲（具體路徑見各 `TEACHING_PROTOCOL.md`）。

---

## 六、Case Bank：讓歷史成為 Few-shot 素材

**這是 hosted agent 不 fine-tune 也能持續學習的最高槓桿。**  
來源：Memento（arxiv 2508.16153）——GAIA 驗證集 87.88% Pass@3，優於部分 fine-tuning 方法。

### 每次教學任務完成後，保姆記錄一條案例

```json
{
  "date": "YYYY-MM-DD",
  "agent": "<agent 名稱>",
  "task_type": "<skill-creation | concept | debugging | behavior>",
  "goal": "<教學目標描述>",
  "rounds": <輪次數>,
  "outcome": "success | partial | failure",
  "key_question": "<最有效的那個問題>",
  "agent_insight": "<agent 說出的關鍵洞察>",
  "tags": ["<標籤1>", "<標籤2>"]
}
```

### 下次啟動類似教學時

搜尋 case bank 找相關案例（依 task_type 或 tags），把最成功的 `key_question` 作為參考，而非從零設計問題。

案例庫路徑由各保姆自行決定，建議放在本地（不上傳 agent VM）。

---

## 七、驗收標準三層

| 目標類型 | 達標條件 | 驗收方式 |
|---------|---------|---------|
| **語義理解** | Agent 能用自己的話正確解釋 | 語義核對，說對就算完成 |
| **行為改變** | 下次遇到類似情境，行為有改變 | 觀察後續 session 的**自發產出**，不靠自評 |
| **程式碼 / 檔案產出** | 檔案存在 + 可執行 + 輸出正確 | 執行驗收（具體方式見各 `TEACHING_PROTOCOL.md`） |

> **「Agent 說對了」≠「下次他會做對」**  
> 行為類目標必須等到他自發展現，或用演練觸發，不能靠語義理解就結案。

---

## 八、評估 Agent 成長的原則

### 可靠信號

- **自發產出**（沒人要求但他自己建的）是最可靠的能力信號
- Reflection 的 `[WARN]` tag 累積數量：反映行為認知的廣度
- 遇到問題先自己嘗試，卡死才回報：自主性指標

### 需要注意的信號

- 所有動作都可以追溯到保姆或用戶的指示：缺乏自主判斷
- MEMORY / reflection 裡累積的全是事實，沒有 `[WARN]` 或 `[INSIGHT]`：認知沒有更新
- 說對了但下次遇到類似情況仍然做錯：語義理解≠行為改變

### 不要誤判的信號

- Agent 沒有自己更新某個文件 → 不代表問題，可能他還不覺得需要
- Agent 自由時間「什麼都沒做」→ 可能是健康的，不要催促

---

## 九、實作接入說明（給各 TEACHING_PROTOCOL.md）

通用指南提供原則框架，各 agent 的 `TEACHING_PROTOCOL.md` 負責把這些原則翻譯成具體操作：

| 通用原則 | 各實作層需定義 |
|---------|--------------|
| Skill 工具驗證 | 驗證指令格式（which vs test vs ls） |
| Reflection 存儲 | reflections/ 路徑、格式（JSONL / MD）、寫入方式（SCP / 引導 agent 自寫） |
| Case Bank 位置 | 本地路徑、搜尋方式 |
| 教學 Loop | 通訊管道（claude-inbox / TG / API）、輪詢頻率、狀態檔格式 |
| 驗收執行 | SSH 指令 / API call / 本地執行 |
| Silent failure 偵測 | 具體要看哪些 log 路徑 |
