"""
file_ops.py — 安全讀寫工具

功能：
  - safe_read：讀檔，失敗回傳 None 而不拋例外
  - safe_write：原子寫入（寫 .tmp → os.replace），防止寫到一半的損壞狀態
  - append_log：追加一行到 log 檔（確保目錄存在）
  - FileLock：context manager，用 O_CREAT|O_EXCL 實作跨平台 file lock
"""

import os
import sys
import time
from datetime import datetime
from pathlib import Path


def safe_read(path: Path | str, encoding: str = "utf-8") -> str | None:
    """讀檔，失敗回傳 None。"""
    try:
        return Path(path).read_text(encoding=encoding, errors="replace")
    except OSError:
        return None


def safe_write(path: Path | str, content: str, encoding: str = "utf-8") -> bool:
    """
    原子寫入：先寫 .tmp，再 os.replace 取代目標。
    確保即使寫到一半程序被殺，目標檔案不會損壞。
    回傳是否成功。
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(content, encoding=encoding)
        os.replace(tmp, path)
        return True
    except OSError as e:
        print(f"[file_ops] safe_write failed for {path}: {e}", file=sys.stderr)
        tmp.unlink(missing_ok=True)
        return False


def append_log(path: Path | str, message: str, encoding: str = "utf-8") -> bool:
    """追加一行到 log 檔。若目錄不存在自動建立。"""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding=encoding) as f:
            f.write(message.rstrip() + "\n")
        return True
    except OSError as e:
        print(f"[file_ops] append_log failed for {path}: {e}", file=sys.stderr)
        return False


class FileLock:
    """
    跨平台 file lock（使用 O_CREAT|O_EXCL，Windows 和 Unix 均支援）。
    使用獨立 .lock 檔，非鎖定目標檔本身。

    用法：
        with FileLock("data/babysit.lock", timeout=30):
            # 在此區段內保證單一進程執行
    """

    def __init__(self, lock_path: Path | str, timeout: int = 60, stale_timeout: int = 600):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self.stale_timeout = stale_timeout

    def acquire(self) -> bool:
        """嘗試取得 lock。成功回傳 True，timeout 回傳 False。"""
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.lock_path.exists():
                age = time.time() - self.lock_path.stat().st_mtime
                if age > self.stale_timeout:
                    print(f"[file_ops] stale lock ({age:.0f}s), removing: {self.lock_path}", file=sys.stderr)
                    self.lock_path.unlink(missing_ok=True)

            try:
                fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(fd, f"{os.getpid()}\n{datetime.now().isoformat()}".encode())
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(1)

        return False

    def release(self):
        self.lock_path.unlink(missing_ok=True)

    def __enter__(self):
        if not self.acquire():
            raise TimeoutError(f"Could not acquire lock: {self.lock_path}")
        return self

    def __exit__(self, *_):
        self.release()
