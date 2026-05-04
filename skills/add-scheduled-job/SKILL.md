---
name: add-scheduled-job
description: 新增本機 claude -p 排程任務到 Symbiont user_jobs（config.yaml）。支援單次 prompt（simple）和多 session 依序執行（pipeline）。適用情境：用戶說「幫我排程 X」「定時跑 Y」「每週執行 Z」。
trigger: /add-scheduled-job
---

## 步驟

### 1. 確認 Symbiont 路徑

Read 用戶的主 `CLAUDE.md`（通常是 `C:\<workdir>\CLAUDE.md`）找 Symbiont 專案路徑。
Read `<symbiont>/config.yaml` 確認 `user_jobs` 區塊現有內容。

### 2. 收集 job 資訊

從對話上下文推斷，不足的才問。需要確認：

| 欄位 | 說明 |
|------|------|
| `name` | 唯一識別名（英文，無空格） |
| `type` | `simple`（單一 prompt）或 `pipeline`（多步驟） |
| 執行時間 | 用戶給本地時間 → 轉換為 UTC cron（台北 UTC+8，台北 12:00 = UTC 04:00） |
| `cooldown_hours` | 兩次執行最短間隔（建議：每日任務 20、每週任務 100） |
| `cwd` | claude 執行的工作目錄（絕對路徑） |
| `prompt` | simple：一個 prompt；pipeline：每個 step 一個 prompt |

**Pipeline prompt 必須以「Read HANDOFF.md first.」開頭**，確保重試時 agent 從正確狀態繼續。

### 3. 確認後寫入 config.yaml

用 Read + Edit 把新 job append 到 `user_jobs:` 陣列。

```yaml
# simple 範例
user_jobs:
  - name: weekly-review
    enabled: true
    type: simple
    cron: "0 1 * * 1"        # UTC；台北時間每週一 09:00
    cooldown_hours: 100
    cwd: "C:/your-projects/my-project"
    prompt: |
      Read HANDOFF.md first. Do weekly review and update HANDOFF.md.

# pipeline 範例
  - name: agora-batch
    enabled: true
    type: pipeline
    cron: "0 4 * * 6"        # UTC；台北時間每週六 12:00
    cooldown_hours: 100
    cwd: "C:/your-projects/agora"
    steps:
      - prompt: |
          Read HANDOFF.md first. Run rounds 13-15. After round 15 run /wrap then exit.
      - prompt: |
          Read HANDOFF.md first. Run rounds 16-18. After round 18 run /wrap then exit.
```

### 4. 驗證 Task Scheduler 任務存在

```bash
schtasks /Query /TN "symbiont-user-jobs"
```

若不存在，執行：
```bash
PYTHONW=$(where pythonw.exe | head -1)
AGENT_DIR="C:\\your-projects\\Symbiont"
schtasks /Create /TN "symbiont-user-jobs" /TR "\"$PYTHONW\" \"$AGENT_DIR\\scripts\\run_user_jobs.py\"" /SC HOURLY /MO 1 /RU "$USERNAME" /F
```

### 5. 回報

告訴用戶：
- job 名稱、cron 時間（UTC 和台北時間對照）
- `config.yaml` 已更新
- Task Scheduler 狀態
