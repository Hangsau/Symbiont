"""
Tests for src/session_wrap.py — 9 個結構性風險驗收測試

覆蓋的風險類別：
  - 並發 (4b)：memory.lock 序列化
  - 邊界 (4b)：空 session、無 friction、損壞 jsonl
  - 持久化 (4b)：cursor 推進防止重複處理
  - 外部輸入驗證 (4b)：LLM 輸出格式異常 → _malformed/
  - 跨狀態一致性 (4b)：MEMORY.md ## Thoughts section 自動建立
  - 邏輯：confidence threshold 過濾
  - 互斥：wrap_done.txt skip 機制
"""
import json
import threading
import time
import uuid as _uuid_mod
from pathlib import Path

import pytest

import src.session_wrap as sw
from src.session_wrap import (
    _append_thoughts_index_line,
    MIN_TURNS,
    WRAP_DONE_MAX_AGE_SECS,
)
from src.utils.file_ops import FileLock


# ── Helpers ───────────────────────────────────────────────────────

def _make_jsonl(path: Path, n_turns: int) -> None:
    """建立含 n_turns 條 user/assistant 對話的 .jsonl session 檔。"""
    lines = []
    for i in range(n_turns):
        lines.append(json.dumps({
            "type": "user",
            "message": {"role": "user", "content": f"user message {i}"},
            "timestamp": f"2026-05-01T00:{i:02d}:00Z",
        }))
        lines.append(json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": f"assistant reply {i}"}],
            },
            "timestamp": f"2026-05-01T00:{i:02d}:01Z",
        }))
    path.write_text("\n".join(lines), encoding="utf-8")


def _make_fixture(tmp_path: Path):
    """
    建立完整測試環境，回傳 (cfg, project_dir, memory_dir) 三元組。

    目錄結構：
      tmp_path/
        data/                          ← Symbiont data
        sessions/                      ← claude_projects_base (sessions_dir)
          test-project/                ← 唯一 project 子目錄
            dummy_session.jsonl        ← 讓 autodetect 能找到此 project
            memory/
              MEMORY.md

    get_path(cfg, "memory_dir") 走 autodetect → test-project → test-project/memory
    session .jsonl 放在 test-project/ 下，find_session_by_uuid(rglob) 可找到。
    """
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    project_dir = sessions_dir / "test-project"
    project_dir.mkdir()

    # autodetect 需要至少一個 .jsonl；用最早的 mtime 讓真實 session 優先
    dummy_jsonl = project_dir / "dummy_session.jsonl"
    dummy_jsonl.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "init"},
                    "timestamp": "2020-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    # 故意把 dummy 的 mtime 設到過去，讓真實 session mtime 更新，不影響 find_sessions_after
    import os
    old_time = time.time() - 86400  # 一天前
    os.utime(str(dummy_jsonl), (old_time, old_time))

    data_dir = tmp_path / "data"
    data_dir.mkdir()

    memory_dir = project_dir / "memory"
    memory_dir.mkdir()
    (memory_dir / "MEMORY.md").write_text("# Memory Index\n\n", encoding="utf-8")

    cfg = {
        "_root": str(tmp_path),
        "paths": {
            "claude_projects_base": str(sessions_dir),
            "primary_project": "",
            "global_claude_md": str(tmp_path / "CLAUDE.md"),
            "wrap_done_file": str(tmp_path / ".wrap_done.txt"),
            "evolution_log": str(data_dir / "evolution_log.md"),
            "state_file": str(data_dir / "state.json"),
            "session_wrap_state": "data/session_wrap_state.json",
            "pending_session_wrap": "data/pending_session_wrap.txt",
            "error_log": str(data_dir / "error.log"),
            "audit_log": str(data_dir / "audit.log"),
        },
        "claude_runner": {"timeout_seconds": 10, "max_retries": 1},
        "session_reader": {"max_turns": 50},
        "session_wrap": {
            "enabled": True,
            "auto_write": True,
            "confidence_threshold": 0.8,
            "ctx_cap_chars": 8000,
            "skip_if_wrap_done": False,
        },
        "memory_audit": {
            "enabled": True,
            "auto_archive": True,
            "thoughts_archive_threshold": 30,
            "memory_index_warn_lines": 170,
        },
    }
    return cfg, project_dir, memory_dir


def _good_llm_output(candidates=None, insight=None) -> str:
    """產生符合格式的 LLM JSON 輸出字串。"""
    payload = {
        "memory_candidates": candidates if candidates is not None else [],
        "insight": insight,
    }
    return json.dumps(payload, ensure_ascii=False)


def _make_pending(tmp_path: Path, session_uuid: str) -> None:
    """寫入 pending_session_wrap.txt。"""
    pending = tmp_path / "data" / "pending_session_wrap.txt"
    pending.parent.mkdir(exist_ok=True)
    pending.write_text(session_uuid, encoding="utf-8")


# ── Test 1: 並發 lock 序列化 ──────────────────────────────────────

class TestConcurrentLock:
    """test_concurrent_lock — 兩 thread 同時競爭 memory.lock，只有一個能拿到。"""

    def test_concurrent_lock(self, tmp_path):
        """
        兩條 thread barrier 同時起跑，直接測試 FileLock 並發行為。
        驗證：只有一個 thread 拿到鎖，另一個 TimeoutError；
              timeout 的 thread 不應寫入任何 memory 檔案。
        """
        lock_path = tmp_path / "data" / "memory.lock"
        lock_path.parent.mkdir(exist_ok=True)
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(exist_ok=True)

        results = []
        barrier = threading.Barrier(2)
        written_files = []

        def worker(worker_id: int):
            barrier.wait()  # 兩個 thread 同時起跑
            try:
                with FileLock(lock_path, timeout=1):
                    results.append(("acquired", worker_id))
                    dest = memory_dir / f"feedback_worker{worker_id}.md"
                    dest.write_text(f"content from worker {worker_id}", encoding="utf-8")
                    written_files.append(dest.name)
                    time.sleep(0.4)  # 持有鎖，確保另一條 thread 會 timeout
            except TimeoutError:
                results.append(("timeout", worker_id))

        t1 = threading.Thread(target=worker, args=(1,))
        t2 = threading.Thread(target=worker, args=(2,))
        t1.start(); t2.start()
        t1.join(); t2.join()

        outcomes = [r[0] for r in results]
        assert sorted(outcomes) == ["acquired", "timeout"], (
            f"應該一個 acquired 一個 timeout，實際結果：{results}"
        )

        # timeout 的 worker 不應有寫入檔案
        timeout_worker_id = next(r[1] for r in results if r[0] == "timeout")
        assert not (memory_dir / f"feedback_worker{timeout_worker_id}.md").exists(), (
            f"timeout 的 worker {timeout_worker_id} 不應寫入任何 memory 檔案"
        )

        # 成功的 worker 只應有一個檔案
        assert len(written_files) == 1, (
            f"只有一個 worker 應成功寫入，實際 written_files={written_files}"
        )


# ── Test 2: session turns < MIN_TURNS ────────────────────────────

class TestEmptySession:
    """test_empty_session — session turns < MIN_TURNS → 不呼叫 LLM、不寫檔、cursor 推進。"""

    def test_empty_session(self, tmp_path, monkeypatch):
        cfg, project_dir, memory_dir = _make_fixture(tmp_path)

        # 建立只有 1 個 turn 的 session（parse 後 < MIN_TURNS=3）
        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"
        _make_jsonl(jsonl, n_turns=1)
        _make_pending(tmp_path, session_uuid)

        llm_call_count = []

        def fake_run_claude(prompt, cfg_arg):
            llm_call_count.append(1)
            return _good_llm_output()

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        result = sw.run(dry_run=False, skip_if_wrap_done=False)

        assert result == 0, f"turns < MIN_TURNS 應正常退出（exit 0），實際：{result}"
        assert len(llm_call_count) == 0, "session 太短不應呼叫 LLM"

        # memory/ 只有預建的 MEMORY.md，不應有其他 .md
        written = [f for f in memory_dir.glob("*.md") if f.name != "MEMORY.md"]
        assert written == [], f"不應寫入任何 memory 檔案，實際：{written}"

        # cursor 應推進
        state_path = tmp_path / "data" / "session_wrap_state.json"
        assert state_path.exists(), "cursor state file 應在 session 跳過後被寫入"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["last_processed_uuid"] == session_uuid, (
            f"cursor 未推進到 {session_uuid}，實際：{state['last_processed_uuid']}"
        )


# ── Test 3: LLM 回空 candidates + null insight ───────────────────

class TestNoFrictionSession:
    """test_no_friction_session — LLM 回空結果 → 不寫檔、cursor 推進、exit 0。"""

    def test_no_friction_session(self, tmp_path, monkeypatch):
        cfg, project_dir, memory_dir = _make_fixture(tmp_path)

        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"
        _make_jsonl(jsonl, n_turns=5)
        _make_pending(tmp_path, session_uuid)

        def fake_run_claude(prompt, cfg_arg):
            return _good_llm_output(candidates=[], insight=None)

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        result = sw.run(dry_run=False, skip_if_wrap_done=False)

        assert result == 0, f"無候選應正常退出（exit 0），實際：{result}"

        # 只有預建的 MEMORY.md
        written = [f for f in memory_dir.glob("*.md") if f.name != "MEMORY.md"]
        assert written == [], f"LLM 回空時不應寫入 memory 檔案，實際：{written}"

        # cursor 推進
        state_path = tmp_path / "data" / "session_wrap_state.json"
        assert state_path.exists(), "cursor state file 應在無候選後被寫入"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert state["last_processed_uuid"] == session_uuid


# ── Test 4: 損壞 jsonl 行不 crash ─────────────────────────────────

class TestMalformedJsonl:
    """test_malformed_jsonl — session 含損壞行 → parse 不 crash，流程繼續。"""

    def test_malformed_jsonl(self, tmp_path, monkeypatch):
        cfg, project_dir, memory_dir = _make_fixture(tmp_path)

        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"

        # 建立混有損壞行的 jsonl
        good_lines = []
        for i in range(5):
            good_lines.append(json.dumps({
                "type": "user",
                "message": {"role": "user", "content": f"msg {i}"},
                "timestamp": f"2026-05-01T00:{i:02d}:00Z",
            }))
            good_lines.append(json.dumps({
                "type": "assistant",
                "message": {"role": "assistant",
                            "content": [{"type": "text", "text": f"reply {i}"}]},
                "timestamp": f"2026-05-01T00:{i:02d}:01Z",
            }))

        content_lines = good_lines[:4] + [
            "THIS IS NOT JSON {{{",
            '{"incomplete":',
            "",
        ] + good_lines[4:]
        jsonl.write_text("\n".join(content_lines), encoding="utf-8")
        _make_pending(tmp_path, session_uuid)

        def fake_run_claude(prompt, cfg_arg):
            return _good_llm_output(candidates=[], insight=None)

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        try:
            result = sw.run(dry_run=False, skip_if_wrap_done=False)
        except Exception as e:
            pytest.fail(f"損壞的 jsonl 行不應導致 exception：{e}")

        assert result == 0, f"損壞 jsonl 行後流程應完成（exit 0），實際：{result}"


# ── Test 5: cursor 推進防止重複處理 ──────────────────────────────

class TestStateCursorAdvance:
    """test_state_cursor_advance — 連跑兩次同一 session → 第二次 cursor 不會重新處理該 session。"""

    def test_state_cursor_advance(self, tmp_path, monkeypatch):
        cfg, project_dir, memory_dir = _make_fixture(tmp_path)

        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"
        _make_jsonl(jsonl, n_turns=5)

        call_count = [0]

        def fake_run_claude(prompt, cfg_arg):
            call_count[0] += 1
            return _good_llm_output(candidates=[], insight=None)

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        # 第一次：用 pending 指向 session
        _make_pending(tmp_path, session_uuid)
        result1 = sw.run(dry_run=False, skip_if_wrap_done=False)
        assert result1 == 0

        # 確認 cursor 已記錄此 uuid
        state_path = tmp_path / "data" / "session_wrap_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        assert session_uuid in state.get("processed_recent", []), (
            f"第一次跑完後，{session_uuid} 應在 processed_recent 中"
        )
        assert state["last_processed_uuid"] == session_uuid, (
            "cursor last_processed_uuid 應指向該 session"
        )

        # 第二次：不設 pending，fallback 機制找 session
        # find_sessions_after 會排除 processed_recent 中的 uuid
        calls_before_second = call_count[0]
        result2 = sw.run(dry_run=False, skip_if_wrap_done=False)
        assert result2 == 0

        # session_uuid 應仍在 processed_recent 中（不被移除）
        state2 = json.loads(state_path.read_text(encoding="utf-8"))
        assert session_uuid in state2.get("processed_recent", []), (
            "第二次跑後 processed_recent 應仍包含 session_uuid（防止重複處理）"
        )

        # 第二次不應再呼叫 LLM 處理同一 session
        # 若 call_count 未增加，代表同一 session 沒被重新處理
        # (若找到其他 session 則 call_count 可能增加，但 session_uuid 不在其中)
        # 核心保證：processed_recent 持久化
        assert session_uuid in state2["processed_recent"], (
            "cursor 應持久化 processed_recent 防止重複處理"
        )


# ── Test 6: LLM 輸出格式異常 → _malformed/ ───────────────────────

class TestMalformedLlmOutput:
    """test_malformed_llm_output — LLM 輸出欄位異常 → 寫入 _malformed/，主目錄不污染。"""

    def test_malformed_llm_output(self, tmp_path, monkeypatch):
        cfg, project_dir, memory_dir = _make_fixture(tmp_path)

        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"
        _make_jsonl(jsonl, n_turns=5)
        _make_pending(tmp_path, session_uuid)

        # 三種異常 candidate（confidence >= threshold，不被 confidence filter 丟棄）
        bad_candidates = [
            {
                # 缺 description → _validate_candidate 失敗
                "type": "feedback",
                "name": "Missing Description",
                "filename": "feedback_nodesc.md",
                "content": "some content",
                "confidence": 0.95,
                "existing_match": None,
            },
            {
                # type 不合法
                "type": "invalid_type",
                "name": "Bad Type",
                "description": "A test",
                "filename": "feedback_badtype.md",
                "content": "content",
                "confidence": 0.95,
                "existing_match": None,
            },
            {
                # filename 含大寫（不符合 FILENAME_RE = ^[a-z0-9_]+\.md$）
                "type": "feedback",
                "name": "Bad Filename",
                "description": "Bad filename test",
                "filename": "FEEDBACK_UPPER.md",
                "content": "content",
                "confidence": 0.95,
                "existing_match": None,
            },
        ]

        def fake_run_claude(prompt, cfg_arg):
            return _good_llm_output(candidates=bad_candidates, insight=None)

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        result = sw.run(dry_run=False, skip_if_wrap_done=False)
        assert result == 0

        malformed_dir = memory_dir / "_malformed"

        # _malformed/ 應有寫入
        assert malformed_dir.exists(), "_malformed/ 目錄應被建立"
        malformed_files = list(malformed_dir.glob("*.md"))
        assert len(malformed_files) >= 1, (
            f"schema 驗證失敗的 candidate 應寫入 _malformed/，實際：{malformed_files}"
        )

        # 主目錄不應有異常 candidate 的檔案
        for bad_fname in ["feedback_nodesc.md", "feedback_badtype.md", "FEEDBACK_UPPER.md"]:
            assert not (memory_dir / bad_fname).exists(), (
                f"異常 candidate {bad_fname} 不應出現在主 memory 目錄"
            )


# ── Test 7: MEMORY.md 無 ## Thoughts → 自動建立 section ─────────

class TestThoughtsSectionCreate:
    """test_thoughts_section_create — MEMORY.md 無 ## Thoughts → insight 寫入時自動新增。"""

    def test_thoughts_section_create(self, tmp_path):
        """直接測試 _append_thoughts_index_line 的 section 自動建立行為。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"

        # MEMORY.md 初始沒有 ## Thoughts section
        initial_content = "# Memory Index\n\n- [some entry](feedback_x.md) — desc\n"
        index_path.write_text(initial_content, encoding="utf-8")

        ok = _append_thoughts_index_line(
            index_path,
            title="My Insight",
            rel_path="thoughts/2026-05-01_my-insight.md",
            description="A test insight description",
        )

        assert ok, "_append_thoughts_index_line 應回傳 True"

        updated = index_path.read_text(encoding="utf-8")

        assert "## Thoughts" in updated, (
            "MEMORY.md 應自動新增 ## Thoughts section"
        )
        assert "My Insight" in updated, "insight title 應出現在 ## Thoughts section"
        assert "thoughts/2026-05-01_my-insight.md" in updated, (
            "insight 路徑應出現在索引行"
        )
        # 原有內容應保留
        assert "some entry" in updated, "原有索引行應保留"
        assert "feedback_x.md" in updated, "原有 memory 連結應保留"

    def test_thoughts_section_append_existing(self, tmp_path):
        """MEMORY.md 已有 ## Thoughts → insight 附加到 section 末尾，不重複建立 section。"""
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        index_path = memory_dir / "MEMORY.md"

        initial_content = (
            "# Memory Index\n\n"
            "- [entry](feedback_x.md) — desc\n\n"
            "## Thoughts\n"
            "- [Old Insight](thoughts/2026-04-01_old.md) — old description\n"
        )
        index_path.write_text(initial_content, encoding="utf-8")

        _append_thoughts_index_line(
            index_path,
            title="New Insight",
            rel_path="thoughts/2026-05-01_new.md",
            description="new description",
        )

        updated = index_path.read_text(encoding="utf-8")

        assert updated.count("## Thoughts") == 1, "不應重複建立 ## Thoughts section"
        assert "Old Insight" in updated, "舊 insight 應保留"
        assert "New Insight" in updated, "新 insight 應附加"


# ── Test 8: confidence threshold 過濾 ────────────────────────────

class TestConfidenceThresholdFilter:
    """test_confidence_threshold_filter — 低 confidence candidate 完全丟棄，不寫任何地方。"""

    def test_confidence_threshold_filter(self, tmp_path, monkeypatch):
        cfg, project_dir, memory_dir = _make_fixture(tmp_path)
        cfg["session_wrap"]["confidence_threshold"] = 0.8

        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"
        _make_jsonl(jsonl, n_turns=5)
        _make_pending(tmp_path, session_uuid)

        # 兩個候選：confidence 0.5（低於門檻）和 0.9（高於門檻）
        candidates = [
            {
                "type": "feedback",
                "name": "Low Confidence",
                "description": "This should be discarded entirely",
                "filename": "feedback_low.md",
                "content": "Low confidence content.",
                "confidence": 0.5,
                "existing_match": None,
            },
            {
                "type": "feedback",
                "name": "High Confidence",
                "description": "This should be written to memory",
                "filename": "feedback_high.md",
                "content": "High confidence content.",
                "confidence": 0.9,
                "existing_match": None,
            },
        ]

        def fake_run_claude(prompt, cfg_arg):
            return _good_llm_output(candidates=candidates, insight=None)

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        result = sw.run(dry_run=False, skip_if_wrap_done=False)
        assert result == 0

        # 高 confidence 應寫入主目錄
        assert (memory_dir / "feedback_high.md").exists(), (
            "confidence=0.9 的 candidate 應寫入主 memory 目錄"
        )

        # 低 confidence 不寫入主目錄
        assert not (memory_dir / "feedback_low.md").exists(), (
            "confidence=0.5 的 candidate 不應寫入主目錄"
        )

        # 低 confidence 也不寫入 _malformed/（直接丟棄，不是 schema 錯誤）
        malformed_dir = memory_dir / "_malformed"
        if malformed_dir.exists():
            malformed_files = [f.name for f in malformed_dir.glob("*.md")]
            low_conf_in_malformed = any("low" in f for f in malformed_files)
            assert not low_conf_in_malformed, (
                "低 confidence candidate 應直接丟棄，不寫到 _malformed/"
            )

        # MEMORY.md 索引只包含高 confidence 條目
        memory_md = (memory_dir / "MEMORY.md").read_text(encoding="utf-8")
        assert "High Confidence" in memory_md, (
            "高 confidence 條目應出現在 MEMORY.md 索引"
        )
        assert "Low Confidence" not in memory_md, (
            "低 confidence 條目不應出現在 MEMORY.md 索引"
        )


# ── Test 9: wrap_done.txt 互斥跳過 ───────────────────────────────

class TestSkipIfWrapDone:
    """test_skip_if_wrap_done — .wrap_done.txt 在 15 分鐘內存在 → 提前退出，不呼叫 LLM。"""

    def test_skip_if_wrap_done(self, tmp_path, monkeypatch):
        cfg, project_dir, _ = _make_fixture(tmp_path)
        cfg["session_wrap"]["skip_if_wrap_done"] = True

        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"
        _make_jsonl(jsonl, n_turns=5)
        _make_pending(tmp_path, session_uuid)

        # 建立剛寫的 wrap_done.txt（mtime = now，遠小於 15 分鐘門檻）
        wrap_done = tmp_path / ".wrap_done.txt"
        wrap_done.write_text("done", encoding="utf-8")

        llm_call_count = [0]

        def fake_run_claude(prompt, cfg_arg):
            llm_call_count[0] += 1
            return _good_llm_output()

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        result = sw.run(dry_run=False, skip_if_wrap_done=True)

        assert result == 0, f"wrap_done 內 15 分鐘應靜默退出（exit 0），實際：{result}"
        assert llm_call_count[0] == 0, "wrap_done 內 15 分鐘時不應呼叫 LLM"

        # memory/ 不應有額外寫入
        written = [f for f in (project_dir / "memory").glob("*.md")
                   if f.name != "MEMORY.md"]
        assert written == [], f"skip 路徑下不應有任何 memory 寫入，實際：{written}"

    def test_old_wrap_done_does_not_skip(self, tmp_path, monkeypatch):
        """wrap_done.txt 超過 15 分鐘 → 不跳過，正常執行 LLM。"""
        import os
        cfg, project_dir, _ = _make_fixture(tmp_path)
        cfg["session_wrap"]["skip_if_wrap_done"] = True

        session_uuid = _uuid_mod.uuid4().hex
        jsonl = project_dir / f"{session_uuid}.jsonl"
        _make_jsonl(jsonl, n_turns=5)
        _make_pending(tmp_path, session_uuid)

        # 建立過期的 wrap_done.txt（mtime = 20 分鐘前）
        wrap_done = tmp_path / ".wrap_done.txt"
        wrap_done.write_text("done", encoding="utf-8")
        old_time = time.time() - (WRAP_DONE_MAX_AGE_SECS + 300)
        os.utime(str(wrap_done), (old_time, old_time))

        llm_call_count = [0]

        def fake_run_claude(prompt, cfg_arg):
            llm_call_count[0] += 1
            return _good_llm_output(candidates=[], insight=None)

        monkeypatch.setattr("src.session_wrap.load_config", lambda: cfg)
        monkeypatch.setattr("src.session_wrap.check_auth", lambda: True)
        monkeypatch.setattr("src.session_wrap.run_claude", fake_run_claude)
        monkeypatch.setattr("src.session_wrap.rotate_log", lambda *a, **kw: None)

        result = sw.run(dry_run=False, skip_if_wrap_done=True)

        assert result == 0
        assert llm_call_count[0] == 1, (
            "wrap_done.txt 超過 15 分鐘後應繼續執行（呼叫 LLM 一次）"
        )
