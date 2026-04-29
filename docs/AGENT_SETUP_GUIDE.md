# 共生計劃：Agent 設置說明

**適用對象**：下載 Symbiont、準備培育自己的 Hermes Agent 的用戶  
**目的**：說明 Symbiont 的設計理念，以及它會幫你做什麼

---

## Symbiont 在做什麼

Symbiont 是一套讓 AI agent 持續成長的外部基礎設施。它的核心理念來自 Harness Engineering 研究：

> **把你希望 agent 具備的能力，系統化地外部化——不期望模型「本身就會」。**

一個語言模型本身沒有跨 session 的記憶、沒有自我修正的機制、沒有累積學習的方式。Symbiont 把這些能力建在模型外部，讓 agent 在每次對話結束後仍然在成長。

---

## 為什麼需要 Foundation 設置

研究顯示，agent 的長期行為品質取決於兩份基礎文件是否存在：

**行為憲法（constitution.md）**  
Constitutional AI 研究（Anthropic）的發現：agent 需要一份統一的行為原則文件，才有辦法在輸出後自我檢視——「這違反我的原則嗎？」沒有這份文件，行為規範就算寫了也不會被一致地應用。

**用戶檔案（USER.md）**  
Hermes agent 的 context 會定期重置。如果 agent 不知道「你是誰、你喜歡什麼風格、你們的工作關係是什麼」，每次重置後都要從零開始建立關係。USER.md 讓這些知識跨 session 存活。

---

## 你不需要自己動手

**直接跟保姆 Claude 對話就好。**

保姆會在適當時機引導你完成所有設置：
- 它會和 agent 一起把散落的行為規範整合成 constitution.md
- 它會問你幾個問題，然後代你寫出 USER.md
- 它會引導 agent 建立自己的任務案例庫，讓歷史成為未來的參考素材

這些機制的細節設計在 `SYMBIOSIS_TEACHING_GUIDE.md`，那份文件是給保姆 Claude 讀的操作手冊。

---

## 背後的研究依據

| 機制 | 研究來源 |
|------|---------|
| 行為憲法（constitution.md） | Constitutional AI — Anthropic |
| 結構化 Reflection | Generative Agents — Stanford 2023（移除後 48 小時內行為退化） |
| Case Bank（任務軌跡庫） | Memento — arxiv 2508.16153（GAIA 87.88% Pass@3，優於部分 fine-tuning） |
| 外部化四模組框架 | Harness Engineering — arxiv 2604.08224 |
| 跨 session 狀態保留 | Anthropic「Effective harnesses for long-running agents」 |

---

## Claude 設置 Hermes Agent 的已知坑（2026-04-29 實戰整理）

> 這一節給執行設置的 Claude 讀，不是給用戶讀的。

### ⚠️ 最重要：`.env` 比 `config.yaml` 優先

hermes 的設計是 `.env` 值覆蓋 `config.yaml`。**修改 provider / model / API key，改的是 `.env`，不是 `config.yaml`。**

```
/home/<user>/.hermes/.env  ← 改這裡
/home/<user>/.hermes/config.yaml  ← 只放 fallback、telegram 等結構設定
```

`.env` 關鍵欄位：
```env
OPENAI_API_KEY=<provider的key>
OPENAI_BASE_URL=<provider的base_url>
HERMES_INFERENCE_PROVIDER=custom
HERMES_INFERENCE_MODEL=<model名稱>
```

### ⚠️ 不需要 LiteLLM

hermes 有原生 `fallback_providers` 語法，直接在 `config.yaml` 設定即可：

```yaml
fallback_providers:
  - model: meta-llama/llama-3.3-70b-instruct:free
    provider: openrouter
  - model: gemini-2.0-flash
    provider: gemini
```

不要安裝 LiteLLM proxy——多一層等於多一個故障點。

### ⚠️ 動手前先讀範例 config

hermes 有完整的範例，包含所有可用欄位和說明：
```bash
cat ~/.hermes/hermes-agent/cli-config.yaml.example
```

### ⚠️ gateway 狀態查詢用 gateway_state.json

`pgrep -f hermes` 不可靠（可能只抓到短暫的父進程）。用：
```bash
cat ~/.hermes/gateway_state.json | grep gateway_state
```
`"gateway_state": "running"` + `"telegram": {"state": "connected"}` 才算真的通。

