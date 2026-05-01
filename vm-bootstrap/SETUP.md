# Hermes Agent VM Bootstrap

你正在一台 Arch Linux VM 上設置 Hermes AI agent。照以下步驟執行，每步完成後確認結果再繼續。

## Step 1：讀取 secrets

檢查 `~/secrets.env` 是否存在。

- **存在** → 讀取檔案內容，提取各個 token/key 值
- **不存在** → 逐一詢問用戶，需要以下欄位：
  - `OPENROUTER_API_KEY`（OpenRouter key，`sk-or-v1-` 開頭）
  - `TELEGRAM_BOT_TOKEN`（Telegram Bot token，@BotFather 取得）
  - `TELEGRAM_ALLOWED_USERS`（你的 Telegram User ID，純整數）

讀取方式：
```bash
source ~/secrets.env
echo "OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:0:12}..."
```
若欄位仍為空，需手動確認 secrets.env 格式（每行 `KEY=VALUE`，注釋必須在獨立行）。

## Step 2：安裝 hermes-agent

執行官方安裝腳本：
```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

安裝完成後：
1. 設定 PATH：`export PATH="/usr/local/bin:$HOME/.local/bin:$PATH"`
2. 驗證 binary 存在：`hermes --version`
3. **TTY warning 是正常的**（`/dev/tty: No such device or address`），不是錯誤，忽略即可。

## Step 3：寫入設定檔

### 3a. 寫入 `~/.hermes/.env`

根據 Step 1 取得的值，建立 `~/.hermes/.env` 檔案，只需三個欄位：

```env
OPENROUTER_API_KEY=<Step 1 取得的值>
TELEGRAM_BOT_TOKEN=<Step 1 取得的值>
TELEGRAM_ALLOWED_USERS=<Step 1 取得的值>
```

**重要**：不要加 `HERMES_INFERENCE_PROVIDER`、`HERMES_INFERENCE_MODEL`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`。model 設定放在 config.yaml，不是 .env。

驗證寫入成功：
```bash
cat ~/.hermes/.env
```

### 3b. 寫入 `~/.hermes/config.yaml`

將 `TELEGRAM_ALLOWED_USERS` 的值填入（純整數，不加引號）：

```yaml
model:
  provider: openrouter
  default: openrouter/free
  context_length: 131072

auxiliary:
  provider: openrouter

telegram:
  allowed_users: <TELEGRAM_ALLOWED_USERS 的數字值，純整數>
  enabled: true
```

**重要**：
- `model.provider: openrouter` 讓 hermes 讀取 `OPENROUTER_API_KEY`
- `model.default: openrouter/free` 讓 OpenRouter 自動選當前可用的免費模型（無需指定 model ID）
- `auxiliary.provider: openrouter` 設定 context compression 用的 auxiliary LLM，避免啟動警告
- `context_length: 131072` 必須 ≥ 64000，否則 hermes 啟動失敗
- `allowed_users` 必須是整數，不要加引號

驗證寫入成功：
```bash
cat ~/.hermes/config.yaml
```

## Step 4：啟動 gateway

執行啟動命令：
```bash
/usr/local/bin/hermes gateway run
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
- `context window below minimum 64,000` → `config.yaml` 的 `context_length` 太小，改成 `131072`
- `No models provided` → model 被設在 `.env` 的 `HERMES_INFERENCE_MODEL`，改成放在 `config.yaml` 的 `model.default`
- `OpenRouter credential pool has no usable entries` → `~/.hermes/auth.json` 裡 openrouter 的 `last_status` 被錯誤標記，用 python3 清掉：
  ```bash
  python3 -c "
  import json
  with open('/root/.hermes/auth.json') as f: d=json.load(f)
  for c in d['credential_pool'].get('openrouter',[]): c['last_status']=None; c['last_error_code']=None; c['last_error_message']=None
  with open('/root/.hermes/auth.json','w') as f: json.dump(d,f,indent=2)
  print('fixed')
  "
  ```
- `telegram.state` 不是 `connected` → 檢查 `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_ALLOWED_USERS` 是否正確

## 最終報告

完成所有步驟後，輸出：
```
=== Bootstrap 完成 ===
[Gateway 狀態摘要]
[成功 / 失敗及具體原因]
```
