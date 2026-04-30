"""
Tests for concurrent lock behavior and babysit._run_once locking

驗證 FileLock 的並發行為正確，以及 babysit._run_once 在並發時只執行一次工作。
"""
import os
import threading
import time
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.utils.file_ops import FileLock
from src import babysit


# ── FileLock Concurrency Tests ──────────────────────────────────────


class TestFileLockConcurrency:
    """FileLock 並發行為驗證"""

    def test_filelock_two_threads_only_one_acquires(self, tmp_path):
        """兩個 thread 同時嘗試取鎖，只有一個成功"""
        lock_path = tmp_path / "test.lock"
        results = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()  # 兩個 thread 同時起跑
            try:
                with FileLock(lock_path, timeout=1):
                    results.append("acquired")
                    time.sleep(0.5)  # 持有一段時間，確保第二個會 timeout
            except TimeoutError:
                results.append("timeout")

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert sorted(results) == ["acquired", "timeout"]

    def test_filelock_release_then_acquire(self, tmp_path):
        """A 取鎖→釋放→B 取鎖，B 應成功不 timeout"""
        lock_path = tmp_path / "test.lock"

        # A 取鎖再釋放
        with FileLock(lock_path, timeout=1):
            pass

        # B 取同一個 lock，應該成功
        with FileLock(lock_path, timeout=1):
            pass

    def test_filelock_stale_lock_force_acquired(self, tmp_path):
        """stale lock (age > stale_timeout) 應被強制接管"""
        lock_path = tmp_path / "test.lock"

        # 建立一個舊的 lock 檔
        lock_path.write_text("stale")
        old = time.time() - 700  # 700 秒前
        os.utime(lock_path, (old, old))

        # stale_timeout=300 應該把 700 秒的 lock 視為 stale，強制接管
        with FileLock(lock_path, timeout=1, stale_timeout=300):
            pass

        # 執行到這裡代表成功


# ── Babysit Concurrency Tests ──────────────────────────────────────


class TestRunOnceConcurrency:
    """_run_once 並發時的 lock 行為驗證"""

    def test_run_once_skips_when_locked(self, tmp_path, monkeypatch):
        """兩個 thread 同時跑 _run_once，_do_babysit_work 應只被呼叫 1 次"""

        # 建立 fake agents.yaml 與 data/ 目錄結構
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "agents.yaml").write_text(
            "agents:\n"
            "  fake:\n"
            "    enabled: true\n"
            "    type: local\n"
            "    inbox_dir: /tmp/in\n"
            "    outbox_dir: /tmp/out\n",
            encoding="utf-8",
        )

        call_count = [0]
        barrier = threading.Barrier(2)

        def slow_work(*args, **kwargs):
            """執行時間夠長，確保第二個 thread 會碰到 lock"""
            call_count[0] += 1
            time.sleep(0.5)

        # Mock _do_babysit_work 為緩慢函式
        monkeypatch.setattr(babysit, "_do_babysit_work", slow_work)

        # Mock auth check 總是成功
        monkeypatch.setattr(babysit, "check_auth", lambda: True)

        cfg = {"_root": str(tmp_path)}
        error_log = tmp_path / "error.log"

        def runner():
            barrier.wait()  # 確保兩個 thread 同時開始
            babysit._run_once(
                dry_run=False,
                base_dir=tmp_path,
                cfg=cfg,
                error_log=error_log,
                lock_max_age=900,
                teaching_timeout=1800,
            )

        t1 = threading.Thread(target=runner)
        t2 = threading.Thread(target=runner)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # 驗證 _do_babysit_work 只被呼叫 1 次
        assert call_count[0] == 1, f"expected 1, got {call_count[0]}"
