# Symbiont 操作手冊

> 本文件供 Claude 閱讀執行。用戶只需告訴 Claude 要做什麼，Claude 會根據此文件操作。

---

## 初始安裝

**用戶說**：「幫我安裝 Symbiont」或「設定 Symbiont，repo 在 [路徑]」

**Claude 執行**：
1. 確認路徑下有 `config.yaml`（確認是 Symbiont repo）
2. 執行 `setup/setup_windows.bat`（Windows）或 `setup/setup_mac.sh`（Mac）
3. 驗證：`python src/evolve.py --dry-run` 能正常執行

---

## 啟用 babysit（Agent 協作自動化）

> **首次連接 Hermes agent？** 先讀 `docs/CHANNEL_PROTOCOL.md`——包含完整的通道建立流程與已知坑。

**用戶說**：「幫我啟用 babysit」或「啟用 agent 保母」

**Claude 執行**：
1. 確認 `data/agents.yaml` 存在（若不存在，從 `data/agents.example.yaml` 複製並填入設定）
2. 確認 `data/agents.yaml` 的 agent 設定正確（ssh_key、ssh_host、inbox_remote 等）
3. 確認 Task Scheduler 任務已建立（執行過 `setup/setup_windows.bat` 即自動建立）：
   ```powershell
   Get-ScheduledTask -TaskName "symbiont-babysit"
   ```
   若不存在，重新執行 `setup/setup_windows.bat`（需先確認 `data/agents.yaml` 存在）。
4. 驗證（等 2 分鐘後查 log）：
   ```bash
   tail -3 data/babysit_hook.log   # 應看到 babysit 執行記錄
   ```

> **注意**：babysit 由 Task Scheduler 每 2 分鐘自動觸發，無需手動啟動 daemon。
> **SSH key**：確認 `~/.ssh/id_ed25519` 存在，且可連線至 agent 所在主機。
> **24/7 執行**：Task Scheduler 只在電腦開著時跑，如需持續運行考慮移至 VM。

---

## 停用 babysit

**用戶說**：「停用 babysit」或「關閉 agent 保母」

**Claude 執行**：
```powershell
# 停用 Task Scheduler 任務（暫停）
schtasks /Change /TN "symbiont-babysit" /DISABLE
# 或永久移除
schtasks /Delete /TN "symbiont-babysit" /F
```

---

## 手動執行 babysit

**用戶說**：「立刻跑一次 babysit」

**Claude 執行**：
```bash
cd [Symbiont 路徑]
python src/babysit.py --dry-run   # 先預覽（不寫 heartbeat、不送訊息）
python src/babysit.py             # 確認後真實執行
```

> Dry-run 會印 prompt preview；正式跑會在第一行 LLM 回應中解析 `MODE: teaching|discussion` 標籤決定後續對話風格。

---

## 檢查 babysit 健康狀態

**用戶說**：「babysit 有沒有正常跑？」「健康檢查」「healthz」

**Claude 執行**：
```bash
cd [Symbiont 路徑]
python src/healthz.py               # 人類可讀；exit 0 健康 / 1 不健康
python src/healthz.py --json        # 機器可讀
python src/healthz.py --max-age 600 # 自訂新鮮度閾值（秒，預設 300）
python src/healthz.py --allow-partial  # 部分 agent SSH fail 仍視為健康
```

判斷邏輯：
- `data/heartbeat.json` 不存在 / 損壞 / 缺欄位 → unhealthy
- `last_run_ts` 距現在超過 `--max-age`（預設 5 分鐘 = 2.5 倍 babysit 週期）→ unhealthy
- 任一 agent SSH ping fail → unhealthy（除非 `--allow-partial` 且至少一個通）
- 全綠 → healthy + `[healthz] OK` + 簡潔狀態行

進階：作業系統層級活體檢查可加查 `Get-ScheduledTask -TaskName 'symbiont-babysit' | Get-ScheduledTaskInfo` 看 `LastTaskResult` / `NumberOfMissedRuns`。

---

## 設定新 Agent

**用戶說**：「加一個新的 agent [名稱]」

**Claude 執行**：
1. 編輯 `data/agents.yaml`，在 `agents:` 下新增條目
2. 填入 `type`（`remote_ssh` 或 `local`）及對應設定
3. 設 `enabled: true`
4. 驗證：`python src/babysit.py --dry-run`

---

## 連接 Hermes Agent 通道（首次建立）

**用戶說**：「幫我把 babysit 連到我的 Hermes agent」或「建立 agent 通道」

**Claude 先讀**：`docs/CHANNEL_PROTOCOL.md`（完整通道建立流程與已知坑）

**Claude 執行順序**：

0. **偵測 hermes-agent 是否已安裝**：
   ```bash
   hermes --version 2>/dev/null || echo "NOT_INSTALLED"
   ```
   - **已安裝** → 直接進步驟 1
   - **未安裝** → 詢問用戶是否需要安裝：
     > 「hermes-agent 尚未安裝。這是 Hermes AI agent 的核心程式，需要先裝才能繼續。要我幫你安裝嗎？」
     - 同意 → 執行安裝：
       ```bash
       curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
       # TTY 警告（/dev/tty: No such device or address）不是 error，正常繼續
       ```
     - 不需要 → 說明 Symbiont 的 babysit 需要 hermes-agent 才能與 AI agent 通訊，流程到此停止

1. **確認部署方式**：問用戶 agent 在哪裡
   - 遠端 VM 或本地 VM (SSH) → `type: remote_ssh`，需要 inbox-watcher + extract_dialogue.py
   - Docker volume mount 或 WSL2 共享目錄 → `type: local`，不需要 watcher（但 teaching loop 不支援）

2. **在 agent 機器上建目錄**（SSH 進去執行）：
   ```bash
   mkdir -p ~/.hermes/for-claude/<agent_name>
   mkdir -p ~/.hermes/claude-inbox
   mkdir -p ~/.hermes/claude-dialogues
   mkdir -p ~/scripts
   ```

3. **寫 inbox-watcher.sh**（直接在 VM 上生成，避免 CRLF 問題）：
   ```bash
   ssh user@<ip> "python3 -c \"open('/home/user/scripts/inbox-watcher.sh', 'w', newline='\\n').write('''<腳本內容>''')\""
   ```
   腳本需做：監控 `claude-inbox/` → 觸發 `hermes cron run` → 延遲後呼叫 `extract_dialogue.py`

4. **寫 extract_dialogue.py**（同樣在 VM 上生成）：
   解析 `~/.hermes/sessions/session_cron_*.json`，取最後一條 `role == "assistant"` 的訊息，寫入 `claude-dialogues/`

5. **部署 systemd service**：
   ```bash
   # 在 VM 上建立 service 檔後：
   systemctl --user enable hermes-claude-inbox.service
   systemctl --user start hermes-claude-inbox.service
   loginctl enable-linger <username>   # 必要：讓 service 在無登入時也能運行
   ```

6. **更新 agents.yaml**（在用戶機器上）

7. **端對端驗證**（不跳過）：
   ```bash
   # 送測試訊息
   echo "Channel test." > /tmp/test.md
   scp /tmp/test.md user@<ip>:~/.hermes/claude-inbox/$(date +%s)_test.md
   # 等 5 分鐘後確認 claude-dialogues/ 有新檔
   ssh user@<ip> "ls -lt ~/.hermes/claude-dialogues/ | head -3"
   ```

8. **通知 agent 管道已開通**：用 `claude-inbox/` 送說明訊息（範本見 CHANNEL_PROTOCOL.md Step 6）

---

## 啟動教學 loop

**用戶說**：「開始教學 [agent名稱]，目標是 [目標]」

**Claude 執行**：
1. 建立（或編輯）`data/teaching_state/[agent名稱].json`：
   ```json
   {
     "status": "active",
     "goal": "[目標描述]",
     "current_round": 1,
     "max_rounds": 20,
     "last_sent_ts": 0,
     "last_processed_dialogue": "",
     "last_question": ""
   }
   ```
2. 用 SCP 送第一個問題到 agent 的 `claude-inbox/`
3. babysit.py 會接手後續自動追蹤

> **逾時行為**：若 agent 超過 30 分鐘未回應，babysit.py 會送一條「你有看到我的問題嗎？」確認訊息，並進入 `timeout_warning` 狀態等待。一旦 agent 回應，教學 loop 自動恢復。

---

## 查看 babysit 記錄

**用戶說**：「看一下 babysit 的記錄」

**Claude 執行**：
- 讀取 `data/babysit.log`（最後 50 行）

---

## 換機遷移 Symbiont

**用戶說**：「我換電腦了，幫我遷移 Symbiont」

**Claude 執行**：
1. 複製整個 Symbiont 目錄到新機
2. 確認 SSH key 存在：`~/.ssh/id_ed25519`（若無，需從舊機複製或重新生成並部署公鑰到 VM）
3. 確認 claude CLI 已登入：`claude --version`
4. 執行 `setup/setup_windows.bat` 重設 Task Scheduler + Stop hook
5. 驗證：`python src/evolve.py --dry-run` 和 `python src/babysit.py --dry-run`

---

## 啟用 memory 系統

**用戶說**：「幫我啟用 memory 系統」或「啟用 Symbiont memory」

**Claude 執行**：
1. 執行 `setup/setup_memory.bat`（Windows）或 `setup/setup_memory.sh`（Mac）
   - 腳本會建立 memory/ 目錄骨架並設 `enabled: true`
2. 確認 Task Scheduler 任務已建立（`setup_windows.bat` 執行後自動建立）：
   ```powershell
   Get-ScheduledTask -TaskName "symbiont-memory-audit" | Get-ScheduledTaskInfo |
     Format-List LastRunTime, NextRunTime, LastTaskResult
   ```
   任務每小時觸發 `scripts/run_audit.py`，wrapper 內部 24h cooldown 控制實際執行。
   - 跳過：距上次跑 < 24h → `sys.exit 0`，audit_hook.log 無新紀錄
   - 執行：first run / 過 24h / `data/last_audit_ts.txt` 損壞或時鐘倒退 → 跑 `memory_audit.py` 並更新 ts
   - 調整 cooldown：改 `config.yaml` 的 `memory_audit.audit_cooldown_hours`（0 = 永遠跑，debug 用）
   - 為何不用固定時間（如 DAILY 04:00）：對筆電/出差/Sleep 用戶不可靠；HOURLY + cooldown 確保開機後 1 小時內補跑
3. 驗證：`python src/memory_audit.py --dry-run`
4. 強制立即跑一次（忽略 cooldown）：`python src/memory_audit.py`（直接呼叫，繞過 wrapper）

---

## 關閉 memory 系統（暫停自動維護）

**用戶說**：「關閉 memory audit」或「停用 memory 自動維護」

**Claude 執行**：
- 編輯 `config.yaml`，將 `memory_audit.enabled` 改為 `false`
- memory/ 目錄保留，只停止自動執行

---

## 只看報告，不自動歸檔

**用戶說**：「memory audit 只報告，不要自動移檔案」

**Claude 執行**：
- 編輯 `config.yaml`，將 `memory_audit.auto_archive` 改為 `false`

---

## 立即執行 memory_audit

**用戶說**：「立刻跑一次 memory audit」

**Claude 執行**：
```bash
cd [Symbiont 路徑]
python src/memory_audit.py --dry-run   # 先預覽
python src/memory_audit.py             # 確認後真實執行
```

---

## 立即執行 evolve

**用戶說**：「立刻跑一次 evolve」或「現在分析這個 session」

**Claude 執行**：
```bash
cd [Symbiont 路徑]
python src/evolve.py --dry-run   # 先預覽
python src/evolve.py             # 確認後真實執行
```

---

## 立即執行 synthesize（跨 session 分析）

**用戶說**：「立刻跑一次 synthesize」或「現在分析最近的 sessions」或「立刻蒸餾記憶」

**Claude 執行**：
```bash
cd [Symbiont 路徑]
python src/synthesize.py --dry-run   # 先預覽（列出會分析哪些 session、蒸餾哪些 memory）
python src/synthesize.py             # 確認後真實執行
```

**預覽輸出說明**：
- `found N sessions` — 準備分析幾個 session
- `fragments: friction=Xc, habit=Yc, total=Zc` — 提取到的 fragment 字數（應 ≤ 12000）
- `distilling N feedback memories...` — 各類型 memory 待蒸餾數量
- `MEMORY.md: X → Y lines` — MEMORY.md 預計壓縮幾行

**查看 synthesis 狀態**：
```bash
cat data/synth_state.json   # 查看 counter（sessions_since_last_synth）和 skill stats
```

---

## 搜尋 knowledge base

**用戶說**：「知識庫有什麼」或「查一下 [關鍵字] 的記憶」或「搜尋 [關鍵字]」

**CLI 搜尋（用戶可直接跑）**：
```bash
cd [Symbiont 路徑]
python src/utils/knowledge_writer.py "git"            # 列出命中條目
python src/utils/knowledge_writer.py "git" --content  # 同時印出檔案內容
```

**Claude 執行**（搜尋並讀取）：
```bash
# 1. 搜尋 tag 索引
Grep "git" knowledge/KNOWLEDGE_TAGS.md

# 2. 找到後 Read 對應檔案
Read knowledge/feedback/git-push-windows.md
```

**查找順序**（Claude 應自動遵守）：
1. `Grep knowledge/KNOWLEDGE_TAGS.md <關鍵字>` → 找到 → Read `knowledge/<type>/<file>.md`
2. 找不到 → `Grep memory/ <關鍵字>`（尚未蒸餾的新記憶）
3. 都找不到 → 問用戶

**列出知識庫內容**：
```bash
ls [primary_project]/knowledge/feedback/
ls [primary_project]/knowledge/project/
ls [primary_project]/knowledge/reference/
```

---

## 重建 knowledge base 索引

**用戶說**：「重建知識庫索引」或「KNOWLEDGE_TAGS.md 好像不對」

**Claude 執行**：
```bash
cd [Symbiont 路徑]
python src/synthesize.py --dry-run   # 確認 knowledge_dir 路徑正確
python -c "
import sys; sys.path.insert(0, '.')
from pathlib import Path
from src.utils.config_loader import load_config, get_path
from src.utils.knowledge_writer import update_knowledge_tags
cfg = load_config()
kdir = get_path(cfg, 'primary_project_dir') / 'knowledge'
update_knowledge_tags(kdir, kdir / 'KNOWLEDGE_TAGS.md')
print('done')
"
```

---

## 查看蒸餾紀錄

**用戶說**：「哪些記憶已被蒸餾」或「查 distilled_mapping」

**Claude 執行**：
```bash
python -c "
import json
from pathlib import Path
state = json.loads(Path('data/synth_state.json').read_text(encoding='utf-8'))
mapping = state.get('distilled_mapping', {})
print(f'已蒸餾: {len(mapping)} 條')
for src, dest in list(mapping.items())[:10]:
    print(f'  {src} → {dest}')
"
```

---

## 移除 Symbiont

**用戶說**：「幫我移除 Symbiont」或「卸載 Symbiont」

**Claude 執行**：
1. 刪除 Task Scheduler 任務：
   ```bash
   schtasks /Delete /TN "symbiont-evolve" /F
   schtasks /Delete /TN "symbiont-memory-audit" /F
   schtasks /Delete /TN "symbiont-babysit" /F
   ```
2. 從 `~/.claude/settings.json` 的 `hooks.Stop` 陣列移除含 `Symbiont-stop-hook` 的條目
3. 刪除旗標檔：
   ```bash
   rm -f ~/.claude/.wrap_done.txt
   rm -f [Symbiont路徑]/data/pending_evolve.txt
   rm -f [Symbiont路徑]/data/pending_audit.txt
   ```
4. 提示用戶手動刪除 Symbiont 資料夾（Claude 無法刪除自己正在讀取的目錄）

---

## 查看 audit 記錄

**用戶說**：「看一下 memory audit 的記錄」

**Claude 執行**：
- 讀取 `data/audit.log`（最後 50 行）

---

## 設定主專案路徑

**用戶說**：「Symbiont 的主專案設成 [路徑]」

**Claude 執行**：
- 編輯 `config.yaml`，將 `paths.primary_project` 改為指定路徑
- 例：`primary_project: "C:/projects/myproject"` 或 `primary_project: "/Users/xxx/myproject"`

---

## 設定 claude CLI 路徑

**用戶說**：「Symbiont 找不到 claude」或 `evolve.py` 報 "claude CLI not found"

**Claude 執行**：

1. 找到 claude CLI 實際位置：
   ```bash
   # Windows (Git Bash)
   where claude   # 通常在 C:\Users\xxx\AppData\Roaming\npm\claude.cmd

   # Mac / Linux
   which claude   # 通常在 /usr/local/bin/claude 或 ~/.nvm/versions/node/vX/bin/claude
   ```

2. 編輯 `config.yaml`：
   ```yaml
   # Windows：設 .cmd 路徑，claude_runner.py 會自動改用 node+cli.js 執行
   claude_cli: "C:/Users/<用戶名>/AppData/Roaming/npm/claude.cmd"

   # Mac / Linux：設完整路徑（若 which 找到的話可直接填）
   claude_cli: "/usr/local/bin/claude"
   # 或 nvm 路徑：
   claude_cli: "/Users/<用戶名>/.nvm/versions/node/v22.x.x/bin/claude"
   ```

3. 驗證：`python src/evolve.py --dry-run`

> **注意（Claude 閱讀）**：`claude_runner.py` 的 `_resolve_cmd()` 在 Windows 會自動將 `.cmd` 路徑轉為 `node + cli.js` 直呼叫；在 Mac/Linux 會用 `shutil.which` 自動掃描。若 `claude_cli: "claude"` 預設值在 hook 背景進程中仍失敗，改用上方的完整路徑設定。

---

## 疑難排解

### evolve.py 跑了但沒寫入任何規則

- 原因 A：LLM 判斷本次 session 無新規則（正常）
- 原因 B：JSON 解析失敗 → 檢查 `data/error.log`
- 驗證：`python src/evolve.py --dry-run` 看 prompt preview 是否合理

### Stop hook 有觸發但 evolve.py 沒跑

```bash
# 確認 pending 旗標有沒有被寫入
cat data/pending_evolve.txt   # 若不存在，hook 本身沒觸發

# 查 evolve 執行記錄
cat data/evolve_hook.log

# Windows：確認 Task Scheduler 任務在跑
schtasks /Query /TN "symbiont-evolve"
```

**Windows 架構說明**：Stop hook 只寫 `pending_evolve.txt`，不做背景執行。
實際執行 evolve.py 的是 Task Scheduler 任務 `symbiont-evolve`（每 1 分鐘用 `pythonw.exe` 靜默 poll）。
若任務不存在，重跑 `setup/setup_windows.bat`。

常見原因：`claude CLI not found`（見上方「設定 claude CLI 路徑」）

### Task Scheduler 沒有觸發

```bash
# Windows：確認任務存在（注意：任務名稱全小寫）
schtasks /Query /TN "symbiont-evolve"
schtasks /Query /TN "symbiont-memory-audit"
schtasks /Query /TN "symbiont-babysit"

# 若不存在，重跑安裝
setup/setup_windows.bat
```

### synthesize 跑兩次但 patterns 階段只跑一次

**原因**：staged commit 的 resume 行為。上次某個階段失敗、`current_run_id` 沒被清掉，下次會 skip 已 done 階段、從失敗階段續跑。

**確認**：
```bash
python -c "
import json
state = json.loads(open('data/synth_state.json', encoding='utf-8').read())
print('current_run_id:', state.get('current_run_id'))
print('patterns_done_at:', state.get('patterns_done_at'))
print('memories_done_at:', state.get('memories_done_at'))
print('distill_done_at:', state.get('distill_done_at'))
print('prune_done_at:', state.get('prune_done_at'))
print('log_done_at:', state.get('log_done_at'))
"
```

**處理**：正常跑第二次會接著做完。若想強制重跑整輪：手動把 `current_run_id` 與所有 `*_done_at` 設為 null。

### memory_audit 沒做事就退出

**原因**：`memory.lock` 被 synthesize 持有時，audit 會印 `[memory_audit] memory.lock busy, skipping` 並 return 0。**這是預期行為**，避免兩個程序同時改 MEMORY.md。

**處理**：等 synthesize 跑完（通常 5-10 分鐘），audit 下次觸發會自動進去。如果 lock 卡死超過 10 分鐘：
```bash
ls -la data/memory.lock     # 看 mtime
# 若 mtime 超過 10 分鐘，FileLock 會自動視為 stale 強制接管，無需手動清
```

### v1 state.json 自動 migrate

第一次跑新版 evolve / synthesize，舊 state.json 會自動轉成 v2 schema、寫一份 `data/state.json.pre_v2_backup` 安全網。後續 read 不會再 migrate（已是 v2）。

如果想強制重新 migrate：刪掉 v2 state，改名 `.pre_v2_backup` 回原檔名，重跑。

---

## 注意事項（Claude 閱讀）

- 所有路徑操作前，先確認 `config.yaml` 存在（確認在 Symbiont 目錄下）
- 修改 `config.yaml` 後，用 `python src/evolve.py --dry-run` 驗證路徑解析正確
- memory/ 目錄位置由 `primary_project` 設定決定，不是 Symbiont 安裝位置
- `enabled: false` 時 memory_audit.py 會靜默跳過（不報錯）
- Windows 上 `claude` 是 `.cmd` 批次檔，`claude_runner.py` 會自動處理；不需手動改用 node
