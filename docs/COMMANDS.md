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
3. 啟動 daemon（Windows）：
   ```powershell
   # 找到 pythonw.exe 路徑（通常在此）
   $pw = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
   # 啟動無視窗常駐 daemon
   Start-Process $pw -ArgumentList "src\babysit.py","--daemon" -WorkingDirectory "[Symbiont路徑]"
   ```
4. 設定開機自動啟動：將以下內容存成 `start-babysit.bat`，放入 Windows Startup 資料夾（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`）：
   ```batch
   @echo off
   set "PYTHONW=[pythonw.exe 完整路徑]"
   cd /d "[Symbiont路徑]"
   start "" "%PYTHONW%" src\babysit.py --daemon
   ```
5. 驗證（等 10 秒後查 log）：
   ```bash
   tail -3 data/error.log   # 應看到 [babysit] daemon 啟動 ...
   ```

> **注意**：babysit 需要電腦開著才能執行。如需 24/7，考慮移至 VM。
> **SSH key**：確認 `~/.ssh/id_ed25519` 存在，且可連線至 agent 所在主機。
> **pythonw.exe**：使用 pythonw 而非 python，才能在背景執行不出現 console 視窗。

---

## 停用 babysit

**用戶說**：「停用 babysit」或「關閉 agent 保母」

**Claude 執行**：
```powershell
# 停止正在執行的 daemon
Get-Process -Name "pythonw" -ErrorAction SilentlyContinue | Stop-Process -Force
# 移除開機自動啟動（若有設定）
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\start-babysit.bat" -ErrorAction SilentlyContinue
```

---

## 手動執行 babysit

**用戶說**：「立刻跑一次 babysit」

**Claude 執行**：
```bash
cd [Symbiont 路徑]
python src/babysit.py --dry-run   # 先預覽
python src/babysit.py             # 確認後真實執行
```

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
   - 腳本會建立 memory/ 目錄骨架並自動設 `enabled: true`
2. 驗證：`python src/memory_audit.py --dry-run`

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

## 查看 knowledge base

**用戶說**：「知識庫有什麼」或「查一下 [關鍵字] 的記憶」

**Claude 執行**：
```bash
# 搜尋知識庫 tag 索引
grep "[關鍵字]" [primary_project]/knowledge/KNOWLEDGE_TAGS.md

# 列出各類別的知識條目
ls [primary_project]/knowledge/feedback/
ls [primary_project]/knowledge/project/
ls [primary_project]/knowledge/reference/
```

找到相關條目後，Read 對應的 `knowledge/<type>/<file>.md`。

**查找順序**（Claude 應自動遵守）：
1. `Grep knowledge/KNOWLEDGE_TAGS.md <關鍵字>` → 找到 → Read `knowledge/<type>/<file>.md`
2. 找不到 → `Grep memory/ <關鍵字>`（尚未蒸餾的新記憶）
3. 都找不到 → 問用戶

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
   schtasks /Delete /TN "Symbiont-evolve" /F
   schtasks /Delete /TN "Symbiont-memory-audit" /F
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
- 例：`primary_project: "C:/claudehome"` 或 `primary_project: "/Users/xxx/myproject"`

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
# 查 hook 觸發記錄
cat data/evolve_hook.log

# 確認 pending 旗標有被清除
cat data/pending_evolve.txt   # 若存在表示上次沒跑完
```

常見原因：`claude CLI not found`（見上方「設定 claude CLI 路徑」）

### Task Scheduler 沒有觸發

```bash
# Windows：確認任務存在
schtasks /Query /TN "Symbiont-evolve"

# 若不存在，重跑安裝
setup/setup_windows.bat
```

---

## 注意事項（Claude 閱讀）

- 所有路徑操作前，先確認 `config.yaml` 存在（確認在 Symbiont 目錄下）
- 修改 `config.yaml` 後，用 `python src/evolve.py --dry-run` 驗證路徑解析正確
- memory/ 目錄位置由 `primary_project` 設定決定，不是 Symbiont 安裝位置
- `enabled: false` 時 memory_audit.py 會靜默跳過（不報錯）
- Windows 上 `claude` 是 `.cmd` 批次檔，`claude_runner.py` 會自動處理；不需手動改用 node
