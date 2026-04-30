# Hermes Agent VM Bootstrap

你正在一台 Arch Linux VM 上設置 Hermes AI agent。照以下步驟執行，每步完成後確認結果再繼續。

## Step 1：讀取 secrets

檢查 `~/secrets.env` 是否存在。

- **存在** → 讀取檔案內容，提取各個 token/key 值
- **不存在** → 逐一詢問用戶，需要以下欄位：
  - `OPENAI_API_KEY`（LLM provider 的 API key，Groq 用 `gsk_` 開頭）
  - `OPENAI_BASE_URL`（Provider endpoint，Groq：`https://api.groq.com/openai/v1`）
  - `HERMES_INFERENCE_PROVIDER`（固定填 `custom`）
  - `HERMES_INFERENCE_MODEL`（模型名稱，Groq：`llama-3.3-70b-versatile`）
  - `OPENROUTER_API_KEY`（OpenRouter key，fallback 用）
  - `TELEGRAM_BOT_TOKEN`（Telegram Bot token，@BotFather 取得）
  - `TELEGRAM_ALLOWED_USERS`（你的 Telegram User ID）
  - `GEMINI_API_KEY`（Google Gemini API key，fallback 用）

讀取方式：
```bash
source ~/secrets.env
echo "OPENAI_API_KEY: ${OPENAI_API_KEY:0:8}..."
```
若欄位仍為空，需手動確認 secrets.env 格式（每行 `KEY=VALUE`，注釋必須在獨立行）。

## Step 2：安裝 hermes-agent

執行官方安裝腳本：
```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

安裝完成後：
1. 設定 PATH：`export PATH="$HOME/.local/bin:$PATH"`
2. 驗證 binary 存在：`which hermes`
3. **TTY warning 是正常的**（`/dev/tty: No such device or address`），不是錯誤，忽略即可。

## Step 3：寫入設定檔

### 3a. 寫入 `~/.hermes/.env`

根據 Step 1 取得的值，建立 `~/.hermes/.env` 檔案，內容格式如下：
```env
OPENAI_API_KEY=<Step 1 取得的值>
OPENAI_BASE_URL=<Step 1 取得的值>
HERMES_INFERENCE_PROVIDER=custom
HERMES_INFERENCE_MODEL=<Step 1 取得的值>
OPENROUTER_API_KEY=<Step 1 取得的值>
TELEGRAM_BOT_TOKEN=<Step 1 取得的值>
TELEGRAM_ALLOWED_USERS=<Step 1 取得的值>
GEMINI_API_KEY=<Step 1 取得的值>
```

驗證寫入成功：
```bash
cat ~/.hermes/.env
```

### 3b. 寫入 `~/.hermes/config.yaml`

建立 `~/.hermes/config.yaml`，將 `TELEGRAM_ALLOWED_USERS` 的值填入（純整數，不加引號）：
```yaml
model:
  context_length: 32768

fallback_providers:
  - model: meta-llama/llama-3.3-70b-instruct:free
    provider: openrouter
  - model: gemini-2.0-flash
    provider: gemini

telegram:
  allowed_users: <TELEGRAM_ALLOWED_USERS 的數字值，純整數>
  enabled: true
```
注意：`allowed_users` 必須是整數，不要加引號。

驗證寫入成功：
```bash
cat ~/.hermes/config.yaml
```

## Step 4：啟動 gateway

執行啟動命令：
```bash
~/.local/bin/hermes gateway run
```

**注意：** hermes gateway 會自動背景執行，命令行會立刻返回。

等候 5 秒：
```bash
sleep 5
```

## Step 5：驗收

檢查 gateway 狀態檔案：
```bash
cat ~/.hermes/gateway_state.json
```

**成功條件：**
- `"gateway_state": "running"` ✓
- `"telegram": {"state": "connected"}` ✓

如果看到以上兩行，表示安裝成功。輸出完整的 `gateway_state.json` 內容作為驗收報告。

**失敗情況：**
- 如果 `gateway_state` 不是 `running` → 查看完整 JSON，輸出錯誤信息
- 如果 `telegram.state` 不是 `connected` → 檢查 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_ALLOWED_USERS` 是否正確
- 如果文件不存在 → hermes 啟動失敗，需要檢查 `~/.hermes/.env` 和 `config.yaml` 是否正確

## 最終報告

完成所有步驟後，輸出：
```
=== Bootstrap 完成 ===
[Gateway 狀態摘要]
[成功 / 失敗及具體原因]
```
