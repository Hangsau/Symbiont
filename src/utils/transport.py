"""
transport.py — Agent 通訊傳輸層抽象

支援兩種 transport：
  - SSHTransport：遠端 VM（SSH + SCP）
  - LocalTransport：同機目錄（file I/O）

契約：
  - list_inbox 回傳的是該 transport 自己 read_file 能直接接收的 token，
    呼叫端不應假設它一定是檔名或絕對路徑。
  - SSHTransport 由呼叫端拼接 {inbox_remote}{filename}。
  - LocalTransport 會將相對路徑解析為 self.inbox / Path(path_str).name。

使用：
  from src.utils.transport import make_transport
  transport = make_transport(agent_cfg)
"""

import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from src.utils.file_ops import safe_write

# ── 常數 ──────────────────────────────────────────────────────────

SSH_CONNECT_TIMEOUT = 10         # SSH ConnectTimeout（秒）
SSH_TIMEOUT_SECONDS = 15         # subprocess SSH 呼叫逾時
SCP_TIMEOUT_SECONDS = 30         # subprocess SCP 呼叫逾時
DIALOGUES_FETCH_COUNT = 10       # list_dialogues 取最新幾筆


# ── Transport 類別 ────────────────────────────────────────────────

def _quote_remote_path(p: str) -> str:
    if p.startswith("~/"):
        return "~/" + shlex.quote(p[2:])
    return shlex.quote(p)


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
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
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
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            return r.returncode == 0
        except Exception:
            return False

    def ping(self) -> bool:
        ok, _ = self._ssh("echo ok")
        return ok

    def list_inbox(self, inbox_remote: str) -> list[str]:
        q = _quote_remote_path(inbox_remote)
        ok, out = self._ssh(f"ls {q} 2>/dev/null")
        if not ok or not out:
            return []
        return [f for f in out.splitlines() if f.strip()]

    def read_file(self, remote_path: str) -> str | None:
        q = _quote_remote_path(remote_path)
        ok, out = self._ssh(f"cat {q}")
        if not ok:
            return None
        return out if out else None

    def send_reply(self, content: str, outbox_remote: str, filename: str,
                   max_retries: int = 3) -> bool:
        """Send via scp. Keep outbox_remote free of shell-special characters."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False, encoding="utf-8") as f:
            f.write(content)
            tmp = f.name
        try:
            for attempt in range(max_retries):
                if self._scp_to(Path(tmp), f"{outbox_remote}{filename}"):
                    return True
                if attempt < max_retries - 1:
                    time.sleep(2)
            return False
        finally:
            os.unlink(tmp)

    def list_dialogues(self, dialogues_remote: str) -> list[str]:
        q = _quote_remote_path(dialogues_remote)
        ok, out = self._ssh(
            f"ls -t {q} 2>/dev/null | head -{DIALOGUES_FETCH_COUNT}"
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
        p = Path(path_str)
        if not p.is_absolute():
            p = self.inbox / p.name
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
            return content if content else None
        except OSError:
            return None

    def send_reply(self, content: str, _outbox_remote: str, filename: str) -> bool:
        """寫入 self.outbox / filename。outbox_remote 參數為 SSH 介面相容性保留，本地模式忽略。"""
        self.outbox.mkdir(parents=True, exist_ok=True)
        return safe_write(self.outbox / filename, content)

    def list_dialogues(self, _dialogues_remote: str = "") -> list[str]:
        return []

    def read_dialogue(self, _dialogues_remote: str, _filename: str) -> str | None:
        return None


# ── 工廠函式 ─────────────────────────────────────────────────────

def make_transport(agent_cfg: dict) -> SSHTransport | LocalTransport:
    """根據 agent_cfg['type'] 建立對應 transport。"""
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
