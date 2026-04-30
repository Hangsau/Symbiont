"""
test_transport_ssh.py — SSHTransport 安全 quoting 行為測試

驗證 M2.3 修改後的 _quote_remote_path 契約：
- ~/ 開頭 → 保留 ~/ + shlex.quote(rest)
- 其他 → 整段 shlex.quote
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from src.utils.transport import SSHTransport, _quote_remote_path


# ── Test 1: test_quote_remote_path_helper ──────────────────────────


def test_quote_remote_path_helper():
    """直接測 _quote_remote_path 函式的契約。"""
    # 情況1：~/ 開頭 → 保留 ~/ + quote 餘下
    result_tilde = _quote_remote_path("~/foo bar/baz.txt")
    assert result_tilde.startswith("~/"), f"Expected ~/ prefix, got: {result_tilde}"
    assert "foo bar/baz.txt" in result_tilde, f"Expected rest content, got: {result_tilde}"
    # shlex.quote 會加單引號因為有空格
    assert "'" in result_tilde, f"Expected quoting due to space, got: {result_tilde}"

    # 情況2：普通路徑（帶空格）→ 整段 quote
    result_abs = _quote_remote_path("/abs/path with space")
    # shlex.quote 會處理空格（加引號或逃逸）
    assert "abs" in result_abs and "path" in result_abs, f"Expected path content, got: {result_abs}"
    assert ("'" in result_abs or "\\" in result_abs), f"Expected quote/escape for space, got: {result_abs}"

    # 情況3：安全字元路徑（無特殊字元）→ shlex.quote 可能不加引號但內容保存
    safe = _quote_remote_path("/safe/path")
    assert "/safe/path" in safe, f"Expected /safe/path in output, got: {safe}"


# ── Test 2: test_ssh_list_inbox_quotes_path ────────────────────────


def test_ssh_list_inbox_quotes_path(monkeypatch):
    """
    Mock subprocess.run，驗證 SSHTransport.list_inbox
    傳給 ssh 的 cmd 字串內含 quoted path。
    """
    from src.utils import transport

    captured = {}

    class FakeResult:
        def __init__(self):
            self.returncode = 0
            self.stdout = "msg_001.md\nmsg_002.md\n"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeResult()

    # Monkeypatch subprocess.run（在 transport 模組裡）
    monkeypatch.setattr(transport.subprocess, "run", fake_run)

    # 建立 transport 並呼叫 list_inbox
    t = SSHTransport(ssh_key="~/.ssh/key", ssh_host="user@host")
    result = t.list_inbox("~/path with space/")

    # 驗證 subprocess.run 收到的 cmd
    ssh_cmd = captured["cmd"]
    assert isinstance(ssh_cmd, list), f"Expected cmd as list, got: {type(ssh_cmd)}"
    assert "ssh" in ssh_cmd[0], f"Expected ssh in first element, got: {ssh_cmd[0]}"

    # 最後一個元素是 remote command
    remote_cmd = ssh_cmd[-1]
    assert "ls" in remote_cmd, f"Expected 'ls' in remote cmd, got: {remote_cmd}"
    assert "~/" in remote_cmd, f"Expected ~/ to be preserved, got: {remote_cmd}"
    assert "'path with space/'" in remote_cmd, f"Expected quoted 'path with space/', got: {remote_cmd}"

    # 驗證回傳值
    assert result == ["msg_001.md", "msg_002.md"], f"Expected file list, got: {result}"


# ── Test 3: test_ssh_read_file_with_tilde ──────────────────────────


def test_ssh_read_file_with_tilde(monkeypatch):
    """
    Mock subprocess.run，驗證 SSHTransport.read_file
    正確保留 ~/ 並 quote 路徑內容。
    """
    from src.utils import transport

    captured = {}

    class FakeResult:
        returncode = 0
        stdout = "file content"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeResult()

    monkeypatch.setattr(transport.subprocess, "run", fake_run)

    t = SSHTransport(ssh_key="~/.ssh/key", ssh_host="user@host")
    # 用包含空格的路徑以驗證 quoting
    result = t.read_file("~/my docs/bar.md")

    remote_cmd = captured["cmd"][-1]
    assert remote_cmd.startswith("cat "), f"Expected 'cat ' prefix, got: {remote_cmd}"
    assert "~/" in remote_cmd, f"Expected ~/ to be preserved, got: {remote_cmd}"
    # 因為有空格，shlex.quote 會加引號
    assert "'my docs/bar.md'" in remote_cmd, f"Expected quoted 'my docs/bar.md', got: {remote_cmd}"

    assert result == "file content", f"Expected file content, got: {result}"


# ── Test 4: test_ssh_list_inbox_failure_returns_empty ────────────────


def test_ssh_list_inbox_failure_returns_empty(monkeypatch):
    """
    當 SSH 指令失敗（returncode != 0）時，list_inbox 應回傳空列表。
    """
    from src.utils import transport

    class FakeResult:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(transport.subprocess, "run",
                        lambda cmd, **kwargs: FakeResult())

    t = SSHTransport(ssh_key="~/.ssh/key", ssh_host="user@host")
    result = t.list_inbox("~/foo/")

    assert result == [], f"Expected empty list on failure, got: {result}"
