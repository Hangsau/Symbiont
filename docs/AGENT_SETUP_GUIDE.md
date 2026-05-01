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

## 兩種設置路徑

### 路徑 A：互動式（推薦給一般用戶）

**直接跟 Claude Code 對話就好。**

告訴 Claude：「幫我設置 Hermes agent」——Claude 會引導你完成所有步驟：
- 詢問你的 API key、Telegram token 等憑證
- 安裝 hermes-agent、寫入設定、啟動 gateway
- 和 agent 一起把行為規範整合成 constitution.md
- 問你幾個問題，代你寫出 USER.md

這些機制的細節設計在 `SYMBIOSIS_TEACHING_GUIDE.md`，那份文件是給保姆 Claude 讀的操作手冊。

---

### 路徑 B：VM 自動部署（進階 / 開發者）

如果你要在一台**乾淨的 Linux VM** 上部署 Hermes agent，不想走互動流程，使用 `vm-bootstrap/`：

```bash
# 步驟 1：把你本機的 Claude Code 憑證 SCP 到 VM
scp ~/.claude/.credentials.json user@your-vm:~/.claude/.credentials.json

# 步驟 2：填入你的 API key / Telegram token
cp vm-bootstrap/secrets.example.env ~/secrets.env
# 編輯 ~/secrets.env，填入真實值
# （跳過此步也可以——Claude 會在執行時逐一詢問）

# 步驟 3：跑 bootstrap
bash vm-bootstrap/run.sh
```

`run.sh` 呼叫 `claude -p`，Claude 讀取 `vm-bootstrap/SETUP.md` 後自動完成：
1. 安裝 hermes-agent（NousResearch 官方腳本）
2. 從 `~/secrets.env` 寫入 `~/.hermes/.env` 和 `config.yaml`
3. 啟動 hermes gateway
4. 驗收：確認 gateway 狀態為 running 且 Telegram 已連線

> **注意**：vm-bootstrap 只負責安裝 Hermes（Phase 1-3）。constitution.md / USER.md 等 Foundation 設置仍需事後透過互動式 Claude Code 完成。

### 路徑 B 完成後：接上 Symbiont babysit

vm-bootstrap 讓 Hermes gateway 起來，但 babysit 還不認識這台 agent。需要在**本機 Symbiont** 的 `data/agents.yaml` 加入條目：

```yaml
agents:
  hestia:                              # 自訂 agent 名稱
    enabled: true
    type: remote_ssh

    ssh_key: "~/.ssh/id_ed25519"
    ssh_host: "root@your-vm-ip"
    ssh_port: 2223                     # 非標準 port 才需要此欄

    # ⚠️ inbox_remote 必須指 archive/，不是根目錄
    inbox_remote: "~/.hermes/for-claude/archive/"
    outbox_remote: "~/.hermes/claude-inbox/"
    dialogues_remote: "~/.hermes/claude-dialogues/"

    teaching_state_file: "data/teaching_state/hestia.json"
    cooldown_seconds: 600
    system_context: |
      （參考 agents.example.yaml 的 system_context 格式填寫）
```

**通訊方向說明：**

```
babysit（本機）  ──寫入──►  ~/.hermes/claude-inbox/  ──►  Hermes Gateway 監聽並轉交 agent
babysit（本機）  ◄─讀取──   ~/.hermes/for-claude/archive/  ◄──  Agent 主動寫入訊息
```

驗收：
```bash
python src/babysit.py --dry-run   # 確認讀得到 agent 訊息（不實際回應）
python src/babysit.py             # 正式執行
```

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

## Claude 設置 Hermes Agent 的已知坑（2026-05-01 實戰更新）

> 這一節給執行設置的 Claude 讀，不是給用戶讀的。

### ⚠️ Model 放 config.yaml，不是 .env

hermes 的 `openrouter` provider 從 `config.yaml` 的 `model.default` 讀取 model。`HERMES_INFERENCE_MODEL` env var 不被 openrouter provider 正確處理。

**正確格式：**
```yaml
# config.yaml
model:
  provider: openrouter
  default: openrouter/free      # ← model 放這裡
  context_length: 131072
```

```env
# .env — 只放 secrets
OPENROUTER_API_KEY=sk-or-v1-...
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USERS=...
```

**錯誤做法（避免）：**
```env
HERMES_INFERENCE_PROVIDER=custom
HERMES_INFERENCE_MODEL=openrouter/free   # ← 這樣設 model 不會生效
OPENAI_API_KEY=...
OPENAI_BASE_URL=https://openrouter.ai/api/v1
```

### ⚠️ context_length 必須 ≥ 64000

hermes 有最低 context window 檢查。`openrouter/free` 是 routing token，hermes 偵測不到實際 context size，會 fallback 到 32768 → 低於 64K 最低限制 → 啟動失敗。

**必須手動設定：**
```yaml
model:
  context_length: 131072   # ← 必填，不能省略
```

### ⚠️ OpenRouter credential pool 誤標 exhausted

hermes 有 credential pool 追蹤機制（`~/.hermes/auth.json`）。若某次 API 失敗的錯誤被誤存到 OpenRouter 的 credential 條目，會標記 `last_status: exhausted`，後續請求全部被略過。

修法：
```bash
python3 -c "
import json
with open('/root/.hermes/auth.json') as f: d=json.load(f)
for c in d['credential_pool'].get('openrouter',[]): c['last_status']=None; c['last_error_code']=None; c['last_error_message']=None
with open('/root/.hermes/auth.json','w') as f: json.dump(d,f,indent=2)
print('fixed')
"
```

### ⚠️ 不需要 LiteLLM

hermes 有原生 `fallback_providers` 語法。不要安裝 LiteLLM proxy——多一層等於多一個故障點。

### ⚠️ gateway 狀態查詢用 gateway_state.json

`pgrep -f hermes` 不可靠。用：
```bash
cat ~/.hermes/gateway_state.json | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['gateway_state'], d['platforms']['telegram']['state'])"
```
`running` + `connected` 才算真的通。

