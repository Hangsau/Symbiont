# recovery/ — Patch Recovery Tools

## 用途

從 `~/.codex/sessions/<date>/rollout-*.jsonl`（codex CLI session log）抽出 OpenAI Apply Patch 格式的 patch，依時間順序套到 working tree。

## 為什麼存在

2026-04-30 一次 spawn 給 Haiku 的 task（M4-A）prompt 沒禁止 git 操作。Haiku 自作主張跑了 `git pull --rebase origin master` + `git reset` 想清理 working tree，過程中把所有先前未 commit 的 src/ 修改吃掉。

災情：M1.1-A、M1.2-A、M1.3-B、M2.1-A、M2.1-B、M2.2、M2.3、M1.1-C 對 src/ 的修改全部消失（13 個 task 累積）。stash 空、dangling commits 與遺失修改無關。

倖存：docs/、tests/（untracked 不受 reset 影響）、codex rollout log（codex 的 session 持續累積寫入，沒被覆蓋）。

搶救流程：
1. 從 codex rollout 抽出 23 個 apply_patch 操作
2. 寫 minimal `apply_patch.py`（OpenAI 格式）
3. 依時間順序套，22/23 成功（#10 是 codex 自己反悔撤 M2.1-A 的 patch，正確跳過）
4. 手動補做 Haiku 的工作（M2.1-A 三行替換、M4-A 五個檔加 print）
5. pytest 102/102 還原成功

## 工具

- `apply_patch.py` — minimal OpenAI Apply Patch 解析器與套用器（支援 Add / Update / Delete File、@@ anchor、多 hunk）
- `apply_all.py` — 從 `patches/` 目錄按字典序（即時間序，因檔名前綴是 timestamp）依序套，失敗繼續

## 使用

```bash
# Dump patches (一次性，來源是 codex rollout):
python3 -c "
import json
from pathlib import Path
src = Path.home() / '.codex/sessions/2026/04/30/rollout-XXX.jsonl'
out = Path('recovery/patches')
out.mkdir(parents=True, exist_ok=True)
idx = 0
with src.open(encoding='utf-8') as f:
    for line in f:
        obj = json.loads(line)
        p = obj.get('payload', {})
        if p.get('type') == 'custom_tool_call' and p.get('name') == 'apply_patch' and p.get('status') == 'completed':
            ts = obj['timestamp'][:19].replace(':', '-')
            (out / f'{idx:02d}_{ts}.patch').write_text(p['input'], encoding='utf-8', newline='\n')
            idx += 1
"

# 套到 Symbiont/:
cd projects/Symbiont
python recovery/apply_all.py
```

`apply_patch.py` 也可獨立呼叫：

```
python recovery/apply_patch.py <root_dir> <patch_file> [--dry-run]
```

## 教訓（已寫入 memory）

派發任務給外部 CLI agent（Haiku、Codex 等）時，prompt 必須**明確禁止任何 git 操作**（reset / pull / checkout / restore / stash drop）。Agent 只准動 task 指定的檔案範圍。
