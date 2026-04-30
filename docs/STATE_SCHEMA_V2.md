# State Schema v2

來源：`docs/CODE_REVIEW_FINDINGS.md` Findings #2、#3。
任務：`docs/IMPROVEMENT_PLAN.md` M1.3-A。
產出時間：2026-04-30
狀態：設計文件，**尚未實作**。實作交給 M1.3-B；synthesize staged commit 部分由 M2.1-B 落地。

---

## 1. 動機

### Finding #2：synthesize cursor 推到 `now` 會丟 backlog

`synthesize.run()` 結尾把 `last_synth_at` 設為當下 ISO 時間（`synthesize.py:802`）。`_find_target_sessions` 又用 `last_synth_at` 當 `after_ts`，搭配 `find_sessions_since(... limit)` 內 `files[-limit:]` 取最新 N 個。結果：若停機累積 25 個 session、`limit=10`，本輪只處理最新 10 個、cursor 推到 `now`，舊 15 個永遠跳過。

### Finding #3：evolve fallback 只看最新 session

`evolve._find_target_session` 在 `pending_evolve.txt` 缺失時，只取 `find_latest_session` 一個和 `last_processed_uuid` 比較。若中間有未處理 session（例：abandoned wrap、Stop hook 失靈）但最新 session 已處理，永遠回 None。

### Finding #5 子問題：synthesize 多階段無原子保證

`synthesize.run()` 一次寫 skills、memories、knowledge、MEMORY.md prune、evolution_log，中途失敗會留下半完成狀態，且下次重跑會重新做已完成的部分（甚至重複扣計數）。

---

## 2. 新 evolve state.json

```json
{
  "last_processed_mtime": 1770000000.0,
  "last_processed_uuid": "abc123-def4-...",
  "processed_recent": [
    "abc123-def4-...",
    "uuid-2",
    "..."
  ],
  "processed_at": "2026-04-30T08:00:00+00:00"
}
```

| 欄位 | 型別 | 用途 |
|------|------|------|
| `last_processed_mtime` | float | 最近一次成功處理 session 的 mtime（Unix epoch） |
| `last_processed_uuid` | str | 同上一筆 session 的 UUID（mtime 相同時的 tie-breaker） |
| `processed_recent` | list[str] | 最近 50 筆已處理 UUID（環狀，超過丟最舊）。用來避免 fallback 把 `pending_evolve.txt` 強制處理過的舊 session 又撈出來重做 |
| `processed_at` | str | ISO timestamp，最後處理時間（log 用，邏輯不依賴） |

`processed_recent` 上限 50 是常數，不可調。原因：實務 backlog 鮮少超過 20，50 已含安全邊際；無上限會讓 state 檔無界增長。

### evolve fallback 新邏輯

```python
def _find_target_session(cfg) -> tuple[Path | None, str | None]:
    # 1. pending_evolve.txt 強制處理（不變）
    ...

    # 2. fallback：找最舊未處理 session
    state = _read_state(state_path)
    excluded = set(state.get("processed_recent", []))
    after_mtime = state.get("last_processed_mtime", 0.0)
    after_uuid = state.get("last_processed_uuid", None)

    candidate = find_sessions_after(
        sessions_dir,
        after_mtime=after_mtime,
        after_uuid=after_uuid,
        excluded_uuids=excluded,
        limit=1,
    )
    if not candidate:
        return None, None
    return candidate[0], candidate[0].stem
```

差異：
- 不再只看 `find_latest_session`，改找「mtime > cursor 且 UUID 不在 excluded 內」的最舊一個
- 用 `processed_recent` 過濾掉曾被 pending 強制處理過的舊 session

### evolve 寫入邏輯

```python
def _write_state(state_path, uuid, jsonl_path, dry_run):
    state = _read_state(state_path)
    recent = state.get("processed_recent", [])
    if uuid not in recent:
        recent.append(uuid)
        recent = recent[-50:]  # 環狀截斷
    new_state = {
        "last_processed_mtime": jsonl_path.stat().st_mtime,
        "last_processed_uuid": uuid,
        "processed_recent": recent,
        "processed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    safe_write(state_path, json.dumps(new_state, indent=2, ensure_ascii=False))
```

注意 cursor 的 mtime **取目前處理 session 的 mtime**，不是 now。即使連續處理多個 session，cursor 也應該等於最後處理那個的 mtime，否則排序晚一些的 session 仍會被掃出來重做。

---

## 3. 新 synth_state.json

```json
{
  "sessions_since_last_synth": 0,

  "last_synth_session_mtime": 1770000000.0,
  "last_synth_session_uuid": "abc123-...",

  "current_run_id": "2026-04-30T08:00:00+00:00",
  "patterns_done_at": "2026-04-30T08:00:00+00:00",
  "memories_done_at": null,
  "distill_done_at": null,
  "prune_done_at": null,
  "log_done_at": null,

  "skill_stats": { ... 不變 ... },
  "distilled_mapping": { ... 不變 ... }
}
```

| 欄位 | 型別 | 用途 |
|------|------|------|
| `sessions_since_last_synth` | int | 計數器（不變）|
| `last_synth_session_mtime` | float | 上次 synthesis 處理過的最舊 session mtime（cursor）|
| `last_synth_session_uuid` | str | tie-breaker |
| `current_run_id` | str \| null | 目前進行中的 run 識別碼（ISO timestamp）。null = 無進行中 run |
| `<stage>_done_at` | str \| null | 該階段完成時的 `current_run_id`，用來判斷是否該 skip |
| `skill_stats` / `distilled_mapping` | (不變) | 維持現有 schema |

**移除** 舊欄位：
- `last_synth_at`（語意改變、改名為 `last_synth_session_mtime`，保留 ISO 格式 timestamp 沒意義）
- `last_synth_uuid` → 改名 `last_synth_session_uuid`

### synthesize 五階段

| 階段 | 內容 | 持鎖 | 失敗代價 |
|------|------|------|---------|
| `patterns` | LLM call → 寫 skills | 不需 memory.lock | 失敗下次重新 LLM call（接受） |
| `memories` | `_write_memories`（含 MEMORY.md append） | 需 memory.lock | 失敗下次重做（已寫的 memory 檔已 idempotent，重寫不出錯）|
| `distill` | `_distill_memories` + `_run_update_knowledge_tags` | 需 memory.lock | 失敗下次重做 |
| `prune` | `_prune_memory_index` | 需 memory.lock | 失敗下次重做 |
| `log` | `_append_evolution_log` | 不需 | 失敗下次重做（append 重複條目，可接受）|

`update_skill_stats` 與 low-usage skill 清掃放在 `patterns` 階段尾或獨立第六階段，不阻塞 memory 系統，看 M2.1-B 實作便利定。

### staged commit 邏輯

```python
def run(dry_run=False):
    state = _load_synth_state(state_path)

    # 判斷是 resume 還是 new run
    if state.get("current_run_id") is None:
        sessions = _find_target_sessions(cfg, state)
        if not sessions:
            return 0
        state["current_run_id"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for k in ("patterns_done_at", "memories_done_at", "distill_done_at",
                  "prune_done_at", "log_done_at"):
            state[k] = None
        _save_synth_state(state_path, state, dry_run)

    run_id = state["current_run_id"]

    # 階段化
    if state["patterns_done_at"] != run_id:
        _do_patterns_phase(...)
        state["patterns_done_at"] = run_id
        _save_synth_state(...)

    with FileLock(memory_lock_path, timeout=30):
        if state["memories_done_at"] != run_id:
            _do_memories_phase(...)
            state["memories_done_at"] = run_id
            _save_synth_state(...)

        if state["distill_done_at"] != run_id:
            _do_distill_phase(...)
            state["distill_done_at"] = run_id
            _save_synth_state(...)

        if state["prune_done_at"] != run_id:
            _do_prune_phase(...)
            state["prune_done_at"] = run_id
            _save_synth_state(...)

    if state["log_done_at"] != run_id:
        _do_log_phase(...)
        state["log_done_at"] = run_id
        _save_synth_state(...)

    # 全部完成 → 更新 cursor、清 run_id
    last_session = max(sessions, key=lambda p: p.stat().st_mtime)
    state["last_synth_session_mtime"] = last_session.stat().st_mtime
    state["last_synth_session_uuid"] = last_session.stem
    state["sessions_since_last_synth"] = 0
    state["current_run_id"] = None
    for k in ("patterns_done_at", "memories_done_at", "distill_done_at",
              "prune_done_at", "log_done_at"):
        state[k] = None
    _save_synth_state(state_path, state, dry_run)
    return 0
```

關鍵點：
- `current_run_id` 不為 null → 上次中斷，繼續同一批 sessions
- `current_run_id` 為 null → 重新挑 sessions
- 但「同一批 sessions」的識別要在 state 裡留——因 `_find_target_sessions` 不能在 resume 時重新挑。**追加欄位** `current_run_sessions: list[str]`（記目前 run 的 session UUID list），M2.1-B 實作時加上。

### `_find_target_sessions` 改寫

```python
def _find_target_sessions(cfg, state) -> list[Path]:
    sessions_dir = get_path(cfg, "sessions_dir")
    limit = get_int(cfg, "synthesize", "sessions_per_cycle", default=10)

    after_mtime = state.get("last_synth_session_mtime", 0.0)
    after_uuid = state.get("last_synth_session_uuid", None)

    return find_sessions_after(
        sessions_dir,
        after_mtime=after_mtime,
        after_uuid=after_uuid,
        excluded_uuids=None,  # synthesize 沒有「特定 UUID 已處理」的概念
        limit=limit,
    )
```

差異：
- 用 mtime 而非 ISO timestamp
- 從 `find_sessions_since(...)[-limit:]` 改為 `find_sessions_after(...)` 且該函式回傳**最舊 limit 個**（不是最新）

---

## 4. session_reader.py 新函式

新增 `find_sessions_after`：

```python
def find_sessions_after(sessions_dir: Path,
                       after_mtime: float,
                       after_uuid: str | None,
                       excluded_uuids: set[str] | None,
                       limit: int) -> list[Path]:
    """回傳符合條件的 sessions，依 mtime 升序，取最舊 limit 個。

    條件：
      mtime > after_mtime
        OR (mtime == after_mtime AND uuid > after_uuid)
      AND uuid not in (excluded_uuids or set())
    """
    excluded = excluded_uuids or set()
    candidates = []
    for p in sessions_dir.rglob("*.jsonl"):
        m = p.stat().st_mtime
        u = p.stem
        if u in excluded:
            continue
        if m > after_mtime:
            candidates.append(p)
        elif m == after_mtime and after_uuid is not None and u > after_uuid:
            candidates.append(p)
    candidates.sort(key=lambda p: (p.stat().st_mtime, p.stem))
    return candidates[:limit]
```

舊 `find_sessions_since` **保留不刪**——用於 CLI 工具與 dry-run 預覽，但 evolve / synthesize 改用 `find_sessions_after`。

`find_latest_session`、`find_session_by_uuid` 不動（其他地方還在用）。

---

## 5. Migration

### 觸發點

第一次以新版 code 跑 evolve / synthesize 時，`_read_state` / `_load_synth_state` 偵測到舊 schema、自動轉換並覆寫 state.json。**不寫獨立 migration script**——inline 處理更不易遺漏。

### 備份

migrate 前在 `_read_state` / `_load_synth_state` 內寫一份 `<file>.pre_v2_backup`（已存在則跳過）。實作：

```python
def _read_state(state_path: Path) -> dict:
    raw = safe_read(state_path)
    if raw is None:
        return _default_state_v2()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _default_state_v2()
    if _is_legacy_schema(data):
        backup_path = state_path.with_name(state_path.name + ".pre_v2_backup")
        if not backup_path.exists():
            backup_path.write_text(raw, encoding="utf-8")
        data = _migrate_v1_to_v2(data, state_path)
    return data
```

### evolve migration 規則

| 舊欄位 | 新欄位 | 規則 |
|--------|--------|------|
| `last_processed_uuid` | `last_processed_uuid` | 直接搬 |
| `last_processed_uuid` | `processed_recent` | `[last_processed_uuid]` 若有；否則 `[]` |
| `processed_at` | `processed_at` | 直接搬 |
| (無) | `last_processed_mtime` | 嘗試從 `last_processed_uuid` 找對應 jsonl 的 mtime；找不到 → `0.0`（強制 fallback 從頭找）|

### synth_state migration 規則

| 舊欄位 | 新欄位 | 規則 |
|--------|--------|------|
| `sessions_since_last_synth` | (同) | 直接搬 |
| `last_synth_at` (ISO) | `last_synth_session_mtime` (float) | 從 `last_synth_uuid` 找對應 jsonl 的 mtime；找不到 → 把 ISO 字串轉成 timestamp |
| `last_synth_uuid` | `last_synth_session_uuid` | 直接搬 |
| `skill_stats` | (同) | 直接搬 |
| `distilled_mapping` | (同) | 直接搬 |
| (無) | `current_run_id` | `null` |
| (無) | 各 `<stage>_done_at` | `null` |
| (無) | `current_run_sessions` | `[]` |

舊欄位 `last_synth_at` migrate 後**移除**，避免下次 read 又當成 legacy。

---

## 6. 驗收情境

### 情境 A：synthesize backlog 不漏不重

前置：在 tmp sessions_dir 內建 25 個 fake jsonl，mtime 升序為 t1..t25。
state.json 初始為 v2 default（`last_synth_session_mtime=0`）。
config `sessions_per_cycle=10`。

連跑三輪 `synthesize.run()`：
- 第一輪選 t1..t10，cursor 設為 t10 mtime + uuid
- 第二輪選 t11..t20，cursor 設為 t20
- 第三輪選 t21..t25（5 個 < limit），cursor 設為 t25

預期：
- 25 個 session 全部被處理過一次
- 沒有任何 session 被處理兩次
- 第四輪呼叫 `find_sessions_after` 回 `[]`（因 cursor 已到 t25 且無更新 session）

### 情境 B：evolve fallback 找最舊未處理

前置：5 個 session 依 mtime 升序為 A、B、C、D、E。state.json：
```json
{
  "last_processed_mtime": tA,
  "last_processed_uuid": "A",
  "processed_recent": ["A", "C", "E"]
}
```

呼叫 `_find_target_session()`（pending_evolve.txt 不存在）。

預期：回傳 B（mtime > tA 且不在 processed_recent）。處理完後 state 變：
```json
{
  "last_processed_mtime": tB,
  "last_processed_uuid": "B",
  "processed_recent": ["A", "C", "E", "B"]
}
```

下一次再跑回 D（同樣邏輯）。再下次回 None。

### 情境 C：synthesize staged commit 中斷續跑

前置：state.json 為 v2、無進行中 run。injected fault：在 `_do_distill_phase` 第一行 raise RuntimeError。

第一次 run：
- 選 sessions、設 `current_run_id=now`、`patterns_done_at=now`、`memories_done_at=now`
- 進 distill 階段 raise → state 內 `distill_done_at=null`、`current_run_id=now`、`current_run_sessions=[uuid1, uuid2, ...]`
- run() return 1

修掉 fault，第二次 run：
- 偵測 `current_run_id` 不為 null → resume 模式，不重挑 sessions（用 `current_run_sessions`）
- patterns_done_at == current_run_id → skip patterns phase
- memories_done_at == current_run_id → skip memories phase
- 跑 distill / prune / log
- 全部完成 → 更新 cursor、清 run_id

預期：每階段只跑一次（spy 驗證 `run_claude` 在 patterns prompt 上只被呼叫一次跨兩次 run）。

### 情境 D：legacy state migration

前置：舊版 state.json：
```json
{"last_processed_uuid": "abc123", "processed_at": "2026-04-29T..."}
```

第一次以新版 evolve 跑（dry-run 或正式皆可）：
- `_read_state` 偵測 legacy → 寫 `state.json.pre_v2_backup`、轉換為 v2 schema
- 後續邏輯走 v2

預期：
- `state.json.pre_v2_backup` 存在且內容是舊 JSON
- `state.json` 為 v2 格式（`last_processed_mtime` 從 `abc123.jsonl` 的 mtime 取得，找不到則 `0.0`）

---

## 7. 不在範圍內

- `evolution_log.md`：append-only，不需要 schema 改
- `audit_log.md`：append-only，不需要 schema 改
- `data/babysit_state.json`：與本次三個問題無關，不動
- `data/teaching_state/<agent>.json`：同上不動
- `synth_state.lock` 的並發保護擴大（只有 `evolve._increment_synth_counter` 取鎖、`_save_synth_state` 不取）：MEMORY_LOCK_PROTOCOL.md 標為已知限制，建議在 M2.1-B 一併補。

---

## 8. 實作範圍對照（給 M1.3-B 與 M2.1-B）

| 檔案 | M1.3-B 範圍 | M2.1-B 範圍 |
|------|------------|------------|
| `src/utils/session_reader.py` | 新增 `find_sessions_after` | — |
| `src/evolve.py` | `_read_state`、`_write_state`、`_find_target_session`、migration helper | — |
| `src/synthesize.py` | `_load_synth_state`、`_find_target_sessions`、migration helper、cursor 更新邏輯 | `run()` 階段化、`current_run_id` 邏輯、staged commit |
| `data/state.json` | 第一次跑時自動 migrate | — |
| `data/synth_state.json` | 第一次跑時自動 migrate | — |
