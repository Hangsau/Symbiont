"""
babysit.py — 自動化 Claude↔Agent 協作層

功能：
  1. 掃描 for-claude/ 新訊息（agent 主動發起）→ 生引導回應 → 送回 claude-inbox/
  2. 追蹤 TEACHING_STATE（Claude 主動教學 loop）→ 評估回應 → 送下一問

觸發方式：
  - Task Scheduler 每 2 分鐘
  - 手動：python src/babysit.py [--dry-run]

絕對禁忌：
  - 不替 agent 解決問題，只引導
  - LLM 輸出解析失敗時只記 error.log，不送任何訊息
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.utils.config_loader import load_config
from src.utils.claude_runner import run_claude, check_auth
from src.utils.file_ops import safe_read, safe_write, append_log

# ── 常數 ──────────────────────────────────────────────────────────

LOCK_FILE = "data/babysit.lock"
LOCK_MAX_AGE_SECONDS = 900       # 15 分鐘：超過視為崩潰遺留

STATE_FILE = "data/babysit_state.json"
LOG_FILE = "data/babysit.log"
ERROR_LOG = "data/error.log"

NO_REPLY = "NO_REPLY_NEEDED"
NEEDS_HUMAN = "NEEDS_HUMAN_REVIEW"

SSH_CONNECT_TIMEOUT = 10         # SSH ConnectTimeout（秒）
SSH_TIMEOUT_SECONDS = 15         # subprocess SSH 呼叫逾時
SCP_TIMEOUT_SECONDS = 30         # subprocess SCP 呼叫逾時
DIALOGUES_FETCH_COUNT = 10       # list_dialogues 取最新幾筆
DRY_RUN_PREVIEW_CHARS = 400      # dry-run prompt 預覽字數
MAX_PROCESSED_INBOX_HISTORY = 200  # babysit_state.json 保留的已處理 inbox 檔名數
TEACHING_TIMEOUT_SECONDS = 1800  # 教學 loop 等待回應的逾時（30 分鐘）
LAST_QUESTION_MAX_CHARS = 300    # teaching state 儲存的 last_question 截斷長度


# ── Lock 管理 ──────────────────────────────────────────────────────

def _acquire_lock(base_dir: Path) -> bool:
    lock = base_dir / LOCK_FILE
    if lock.exists():
        age = time.time() - lock.stat().st_mtime
        if age < LOCK_MAX_AGE_SECONDS:
            return False
        append_log(base_dir / ERROR_LOG,
                   f"[babysit] lock 超過 {LOCK_MAX_AGE_SECONDS}s，強制刪除（上次可能崩潰）")
        lock.unlink(missing_ok=True)
    lock.write_text(str(datetime.now(timezone.utc).isoformat()))
    return True


def _release_lock(base_dir: Path) -> None:
    (base_dir / LOCK_FILE).unlink(missing_ok=True)


# ── State 管理 ────────────────────────────────────────────────────

def _load_state(base_dir: Path) -> dict:
    raw = safe_read(base_dir / STATE_FILE)
    if raw:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


def _save_state(base_dir: Path, state: dict, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would write state: {json.dumps(state, indent=2)}")
    else:
        safe_write(base_dir / STATE_FILE, json.dumps(state, indent=2, ensure_ascii=False))


# ── Transport 層 ──────────────────────────────────────────────────

class SSHTransport:
    """遠端 SSH agent transport。"""

    def __init__(self, ssh_key: str, ssh_host: str):
        self.key = str(Path(ssh_key).expanduser())
        self.host = ssh_host

    def _ssh(self, cmd: str, timeout: int = SSH_TIMEOUT_SECONDS) -> tuple[bool, str]:
        try:
            r = subprocess.run(
                ["ssh", "-i", self.key,
                 "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT}",
                 "-o", "BatchMode=yes",
                 self.host, cmd],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return r.returncode == 0, r.stdout.strip()
        except subprocess.TimeoutExpired:
            return False, "ssh timeout"
        except Exception as e:
            return False, str(e)

    def _scp_to(self, local_path: Path, remote_path: str,
                timeout: int = SCP_TIMEOUT_SECONDS) -> bool:
        try:
            r = subprocess.run(
                ["scp", "-i", self.key, "-o", "BatchMode=yes",
                 str(local_path), f"{self.host}:{remote_path}"],
                capture_output=True, timeout=timeout,
            )
            return r.returncode == 0
        except Exception:
            return False

    def ping(self) -> bool:
        ok, _ = self._ssh("echo ok")
        return ok

    def list_inbox(self, inbox_remote: str) -> list[str]:
        ok, out = self._ssh(f"ls {inbox_remote} 2>/dev/null")
        if not ok or not out:
            return []
        return [f for f in out.splitlines() if f.strip()]

    def read_file(self, remote_path: str) -> str | None:
        ok, out = self._ssh(f"cat {remote_path}")
        return out if ok else None

    def send_reply(self, content: str, outbox_remote: str, filename: str) -> bool:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp = f.name
        try:
            return self._scp_to(Path(tmp), f"{outbox_remote}{filename}")
        finally:
            os.unlink(tmp)

    def list_dialogues(self, dialogues_remote: str) -> list[str]:
        ok, out = self._ssh(
            f"ls -t {dialogues_remote} 2>/dev/null | head -{DIALOGUES_FETCH_COUNT}"
        )
        if not ok or not out:
            return []
        return [f for f in out.splitlines() if f.strip()]

    def read_dialogue(self, dialogues_remote: str, filename: str) -> str | None:
        return self.read_file(f"{dialogues_remote}{filename}")


class LocalTransport:
    """本地目錄 agent transport。"""

    def __init__(self, inbox_dir: str, outbox_dir: str):
        self.inbox = Path(inbox_dir).expanduser()
        self.outbox = Path(outbox_dir).expanduser()

    def ping(self) -> bool:
        return self.inbox.exists()

    def list_inbox(self, _inbox_remote: str = "") -> list[str]:
        if not self.inbox.exists():
            return []
        return [f.name for f in sorted(self.inbox.iterdir(), key=lambda p: p.stat().st_mtime)]

    def read_file(self, path_str: str) -> str | None:
        try:
            return Path(path_str).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def send_reply(self, content: str, _outbox_remote: str, filename: str) -> bool:
        self.outbox.mkdir(parents=True, exist_ok=True)
        (self.outbox / filename).write_text(content, encoding="utf-8")
        return True

    def list_dialogues(self, _dialogues_remote: str = "") -> list[str]:
        return []

    def read_dialogue(self, _dialogues_remote: str, _filename: str) -> str | None:
        return None


def _make_transport(agent_cfg: dict):
    t = agent_cfg.get("type", "remote_ssh")
    if t == "remote_ssh":
        return SSHTransport(
            ssh_key=agent_cfg["ssh_key"],
            ssh_host=agent_cfg["ssh_host"],
        )
    if t == "local":
        return LocalTransport(
            inbox_dir=agent_cfg["inbox_dir"],
            outbox_dir=agent_cfg["outbox_dir"],
        )
    raise ValueError(f"未知 transport type: {t}")


# ── Teaching State ────────────────────────────────────────────────

def _load_teaching_state(base_dir: Path, state_file: str) -> dict:
    raw = safe_read(base_dir / state_file)
    if raw:
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return {"status": "idle"}


def _save_teaching_state(base_dir: Path, state_file: str,
                          state: dict, dry_run: bool) -> None:
    path = base_dir / state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        print(f"[dry-run] would write teaching state: {state.get('status')}")
    else:
        safe_write(path, json.dumps(state, indent=2, ensure_ascii=False))


# ── Prompt 組裝 ───────────────────────────────────────────────────

def _build_inbox_prompt(agent_name: str, system_context: str,
                         message: str, teaching_state: dict) -> str:
    ts_summary = ""
    if teaching_state.get("status") in ("active", "waiting_reply"):
        goal = teaching_state.get("goal", "")
        last_q = teaching_state.get("last_question", "")
        ts_summary = f"\n\n【教學目標】{goal}\n【上一個問題】{last_q}"

    return f"""{system_context}{ts_summary}

---

【來自 {agent_name} 的訊息】
{message}

---

請用繁體中文回應。記住：引導思考，不替他解決問題。
若這條訊息不需要回覆（如純報告、資訊分享），直接輸出字面值：{NO_REPLY}
若需要人工判斷，輸出：{NEEDS_HUMAN}: [原因]"""


def _build_teaching_prompt(agent_name: str, system_context: str,
                            reply_content: str, teaching_state: dict) -> str:
    goal = teaching_state.get("goal", "")
    last_q = teaching_state.get("last_question", "")
    current_round = teaching_state.get("current_round", 1)
    max_rounds = teaching_state.get("max_rounds", 20)

    return f"""{system_context}

【教學目標】{goal}
【第 {current_round}/{max_rounds} 輪】
【上一個問題】{last_q}
【{agent_name} 的回應】
{reply_content}

---

請評估回應後，設計下一個引導問題。規則：
- 一次只問一件事
- 承接上一輪回應
- 若目標已達成，輸出字面值：GOAL_ACHIEVED
- 若需要人工判斷（如草稿審閱），輸出：{NEEDS_HUMAN}: [原因]
請用繁體中文回應。"""


# ── 主要邏輯：處理 inbox 訊息 ─────────────────────────────────────

def _process_inbox(agent_name: str, agent_cfg: dict, transport,
                   state: dict, cfg: dict, dry_run: bool, base_dir: Path) -> dict:
    """處理 for-claude/ 新訊息。回傳更新後的 state。"""
    inbox_remote = agent_cfg.get("inbox_remote", "")
    outbox_remote = agent_cfg.get("outbox_remote", "")
    cooldown = agent_cfg.get("cooldown_seconds", 600)
    system_context = agent_cfg.get("system_context", "")

    agent_state = state.get(agent_name, {})
    processed = set(agent_state.get("processed_inbox", []))
    last_reply_ts = agent_state.get("last_reply_ts", 0)

    files = transport.list_inbox(inbox_remote)
    new_files = [f for f in files if f not in processed]

    if not new_files:
        return state

    if time.time() - last_reply_ts < cooldown:
        remaining = int(cooldown - (time.time() - last_reply_ts))
        append_log(base_dir / LOG_FILE,
                   f"[{agent_name}] cooldown 中，剩 {remaining}s，跳過 {len(new_files)} 條訊息")
        return state

    # 只處理最新一條（避免一次送太多）
    target = new_files[-1]
    remote_path = f"{inbox_remote}{target}"
    content = transport.read_file(remote_path)

    if content is None:
        append_log(base_dir / ERROR_LOG, f"[{agent_name}] 無法讀取 {remote_path}")
        return state

    # 跳過 babysit 自己生成的訊息（防無限 loop）
    if "generated_by: babysit" in content:
        processed.add(target)
        agent_state["processed_inbox"] = list(processed)
        state[agent_name] = agent_state
        return state

    ts_file = agent_cfg.get("teaching_state_file", f"data/teaching_state/{agent_name}.json")
    teaching_state = _load_teaching_state(base_dir, ts_file)
    prompt = _build_inbox_prompt(agent_name, system_context, content, teaching_state)

    print(f"[{agent_name}] 處理 inbox: {target}")

    if dry_run:
        print(f"[dry-run] prompt preview:\n{prompt[:DRY_RUN_PREVIEW_CHARS]}...")
        processed.add(target)
        agent_state["processed_inbox"] = list(processed)
        state[agent_name] = agent_state
        return state

    response = run_claude(prompt, cfg)
    if response is None:
        append_log(base_dir / ERROR_LOG, f"[{agent_name}] claude -p 失敗，跳過 {target}")
        return state

    if response.strip() == NO_REPLY:
        append_log(base_dir / LOG_FILE, f"[{agent_name}] {target} → NO_REPLY_NEEDED")
    elif response.strip().startswith(NEEDS_HUMAN):
        append_log(base_dir / LOG_FILE,
                   f"[{agent_name}] {target} → {response.strip()[:120]}")
        print(f"⚠️  [{agent_name}] 需要人工介入：{response.strip()}")
    else:
        ts = int(time.time())
        filename = f"babysit_{ts}.txt"
        ok = transport.send_reply(f"generated_by: babysit-{ts}\n\n{response}",
                                   outbox_remote, filename)
        if ok:
            append_log(base_dir / LOG_FILE, f"[{agent_name}] 回應已送出 → {filename}")
            agent_state["last_reply_ts"] = ts
        else:
            append_log(base_dir / ERROR_LOG, f"[{agent_name}] SCP 失敗：{filename}")

    processed.add(target)
    agent_state["processed_inbox"] = list(processed)[-MAX_PROCESSED_INBOX_HISTORY:]
    state[agent_name] = agent_state
    return state


# ── 主要邏輯：教學 loop ───────────────────────────────────────────

def _process_teaching_loop(agent_name: str, agent_cfg: dict, transport,
                            cfg: dict, dry_run: bool, base_dir: Path) -> None:
    """若 TEACHING_STATE 為 active/waiting_reply，查回應並送下一問。"""
    ts_file = agent_cfg.get("teaching_state_file", f"data/teaching_state/{agent_name}.json")
    teaching_state = _load_teaching_state(base_dir, ts_file)
    status = teaching_state.get("status", "idle")

    if status not in ("active", "waiting_reply"):
        return

    dialogues_remote = agent_cfg.get("dialogues_remote", "")
    outbox_remote = agent_cfg.get("outbox_remote", "")
    system_context = agent_cfg.get("system_context", "")
    last_processed = teaching_state.get("last_processed_dialogue", "")
    last_sent_ts = teaching_state.get("last_sent_ts", 0)

    dialogues = transport.list_dialogues(dialogues_remote)
    if not dialogues:
        return

    latest = dialogues[0]
    if latest == last_processed:
        # 無新回應：逾時檢查
        if (time.time() - last_sent_ts > TEACHING_TIMEOUT_SECONDS
                and status != "timeout_warning"):
            ts = int(time.time())
            confirm_msg = (f"generated_by: babysit-{ts}\n\n"
                           f"你好，我在等你回應我上一個問題，你有看到嗎？\n"
                           f"（上一問：{teaching_state.get('last_question', '')}）")
            if not dry_run:
                transport.send_reply(confirm_msg, outbox_remote, f"babysit_{ts}_confirm.txt")
            teaching_state["status"] = "timeout_warning"
            teaching_state["timeout_warning_ts"] = ts
            _save_teaching_state(base_dir, ts_file, teaching_state, dry_run)
            append_log(base_dir / LOG_FILE, f"[{agent_name}] teaching loop: 逾時確認訊息已送")
        return

    reply_content = transport.read_dialogue(dialogues_remote, latest)
    if not reply_content:
        return

    print(f"[{agent_name}] teaching loop: 新回應 {latest}")

    current_round = teaching_state.get("current_round", 1)
    max_rounds = teaching_state.get("max_rounds", 20)

    if current_round >= max_rounds:
        teaching_state["status"] = "completed"
        teaching_state["completed_at"] = datetime.now(timezone.utc).isoformat()
        teaching_state["completion_summary"] = "達到最大輪次上限"
        _save_teaching_state(base_dir, ts_file, teaching_state, dry_run)
        append_log(base_dir / LOG_FILE, f"[{agent_name}] teaching loop: 達到最大輪次，結束")
        return

    prompt = _build_teaching_prompt(agent_name, system_context, reply_content, teaching_state)

    if dry_run:
        print(f"[dry-run] teaching prompt preview:\n{prompt[:DRY_RUN_PREVIEW_CHARS]}...")
        return

    response = run_claude(prompt, cfg)
    if response is None:
        append_log(base_dir / ERROR_LOG, f"[{agent_name}] teaching loop: claude -p 失敗")
        return

    if response.strip() == "GOAL_ACHIEVED":
        teaching_state["status"] = "completed"
        teaching_state["completed_at"] = datetime.now(timezone.utc).isoformat()
        teaching_state["completion_summary"] = "目標達成"
        teaching_state["last_processed_dialogue"] = latest
        _save_teaching_state(base_dir, ts_file, teaching_state, dry_run)
        append_log(base_dir / LOG_FILE, f"[{agent_name}] teaching loop: 目標達成，結束")
        print(f"🎓 [{agent_name}] 教學目標達成！")
        return

    if response.strip().startswith(NEEDS_HUMAN):
        teaching_state["status"] = "needs_review"
        teaching_state["last_processed_dialogue"] = latest
        _save_teaching_state(base_dir, ts_file, teaching_state, dry_run)
        append_log(base_dir / LOG_FILE,
                   f"[{agent_name}] teaching loop: 需人工介入 → {response.strip()[:120]}")
        print(f"⚠️  [{agent_name}] 教學 loop 需要人工介入：{response.strip()}")
        return

    ts = int(time.time())
    filename = f"babysit_{ts}_teach.txt"
    ok = transport.send_reply(f"generated_by: babysit-{ts}\n\n{response}",
                               outbox_remote, filename)
    if ok:
        teaching_state["current_round"] = current_round + 1
        teaching_state["last_sent_ts"] = ts
        teaching_state["last_processed_dialogue"] = latest
        teaching_state["last_question"] = response[:LAST_QUESTION_MAX_CHARS]
        teaching_state["status"] = "waiting_reply"
        _save_teaching_state(base_dir, ts_file, teaching_state, dry_run)
        append_log(base_dir / LOG_FILE,
                   f"[{agent_name}] teaching loop: Round {current_round + 1} 送出")
    else:
        append_log(base_dir / ERROR_LOG, f"[{agent_name}] teaching loop: SCP 失敗")


# ── 主程式 ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="babysit.py — Agent 協作自動化")
    parser.add_argument("--dry-run", action="store_true",
                        help="預覽模式：不送訊息、不寫狀態")
    args = parser.parse_args()
    dry_run = args.dry_run

    base_dir = Path(__file__).parent.parent
    cfg = load_config()
    error_log = base_dir / ERROR_LOG

    if not check_auth():
        append_log(error_log, "[babysit] auth check failed，跳過")
        sys.exit(0)

    agents_file = base_dir / "data/agents.yaml"
    if not agents_file.exists():
        print(f"[babysit] agents.yaml 不存在：{agents_file}")
        sys.exit(0)

    try:
        with open(agents_file, encoding="utf-8") as f:
            agents_cfg = yaml.safe_load(f)
    except Exception as e:
        append_log(error_log, f"[babysit] 無法解析 agents.yaml: {e}")
        sys.exit(0)

    agents = agents_cfg.get("agents", {})
    enabled_agents = {k: v for k, v in agents.items() if v.get("enabled", False)}

    if not enabled_agents:
        print("[babysit] 沒有啟用的 agent，結束")
        sys.exit(0)

    if not dry_run and not _acquire_lock(base_dir):
        print("[babysit] 上一次執行仍在進行，跳過")
        sys.exit(0)

    try:
        state = _load_state(base_dir)

        for agent_name, agent_cfg in enabled_agents.items():
            print(f"\n[babysit] 處理 agent: {agent_name}")
            try:
                transport = _make_transport(agent_cfg)
            except ValueError as e:
                append_log(error_log, f"[{agent_name}] transport 建立失敗: {e}")
                continue

            if not transport.ping():
                append_log(error_log, f"[{agent_name}] 無法連線，跳過")
                print(f"[{agent_name}] ❌ 連線失敗，跳過")
                continue

            state = _process_inbox(
                agent_name, agent_cfg, transport,
                state, cfg, dry_run, base_dir,
            )
            _process_teaching_loop(
                agent_name, agent_cfg, transport,
                cfg, dry_run, base_dir,
            )

        _save_state(base_dir, state, dry_run)

    finally:
        if not dry_run:
            _release_lock(base_dir)

    print("\n[babysit] 完成")


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    main()
