"""
Tests for src/utils/transport.py — LocalTransport class.

Tests the local file system transport implementation, verifying:
- Path contract: relative paths are resolved to inbox by name
- File operations: read_file, send_reply, list_inbox
- Edge cases: empty inbox, missing files, sorted mtime listing
"""
import pytest
import time
from pathlib import Path

from src.utils.transport import LocalTransport


class TestLocalTransportRoundTrip:
    """Test basic round-trip: write to inbox, read back, reply to outbox."""

    def test_local_transport_round_trip(self, tmp_path):
        """Test full cycle: list inbox → read → send reply."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        # Write a test message to inbox
        msg_file = inbox_dir / "msg_001.md"
        msg_file.write_text("hello", encoding="utf-8")

        # Create LocalTransport instance
        t = LocalTransport(str(inbox_dir), str(outbox_dir))

        # Test list_inbox
        assert t.list_inbox("") == ["msg_001.md"]

        # Test read_file
        assert t.read_file("msg_001.md") == "hello"

        # Test send_reply
        assert t.send_reply("reply", "", "out_001.md") is True
        assert (outbox_dir / "out_001.md").read_text(encoding="utf-8") == "reply"


class TestLocalTransportEmptyInbox:
    """Test behavior with empty or non-existent inbox."""

    def test_empty_inbox_returns_empty_list(self, tmp_path):
        """Empty inbox directory should return empty list."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        t = LocalTransport(str(inbox_dir), str(outbox_dir))
        assert t.list_inbox("") == []

    def test_missing_inbox_returns_empty_list(self, tmp_path):
        """Non-existent inbox directory should return empty list, not raise."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        outbox_dir.mkdir()
        # inbox_dir intentionally not created

        t = LocalTransport(str(inbox_dir), str(outbox_dir))
        assert t.list_inbox("") == []


class TestLocalTransportPathContract:
    """Test path resolution contract: relative paths use Path.name to resolve to inbox."""

    def test_read_with_prefix_resolves_to_inbox(self, tmp_path):
        """Reading 'some/prefix/msg_001.md' should extract 'msg_001.md' and find it in inbox."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        msg_file = inbox_dir / "msg_001.md"
        msg_file.write_text("hello", encoding="utf-8")

        t = LocalTransport(str(inbox_dir), str(outbox_dir))

        # Path with prefix should still resolve to inbox by name
        assert t.read_file("some/prefix/msg_001.md") == "hello"

    def test_read_absolute_path_used_directly(self, tmp_path):
        """Absolute paths should be used directly, not resolved to inbox."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        # Write file outside inbox
        external_file = tmp_path / "external.md"
        external_file.write_text("external content", encoding="utf-8")

        t = LocalTransport(str(inbox_dir), str(outbox_dir))

        # Absolute path should read the file directly
        assert t.read_file(str(external_file)) == "external content"


class TestLocalTransportMtimeSorting:
    """Test that list_inbox returns files sorted by mtime (oldest first)."""

    def test_list_sorted_by_mtime(self, tmp_path):
        """list_inbox should return files in ascending mtime order."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        t = LocalTransport(str(inbox_dir), str(outbox_dir))

        # Write files with slight delays to ensure different mtimes
        file1 = inbox_dir / "msg_001.md"
        file1.write_text("first", encoding="utf-8")
        time.sleep(0.01)

        file2 = inbox_dir / "msg_002.md"
        file2.write_text("second", encoding="utf-8")
        time.sleep(0.01)

        file3 = inbox_dir / "msg_003.md"
        file3.write_text("third", encoding="utf-8")

        # list_inbox should return in mtime order (oldest first)
        result = t.list_inbox("")
        assert result == ["msg_001.md", "msg_002.md", "msg_003.md"]


class TestLocalTransportMissingFile:
    """Test read_file behavior with missing files."""

    def test_read_missing_file_returns_none(self, tmp_path):
        """Reading a non-existent file should return None."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        t = LocalTransport(str(inbox_dir), str(outbox_dir))

        assert t.read_file("nonexistent.md") is None

    def test_read_empty_file_returns_none(self, tmp_path):
        """Reading an empty file should return None (contract: empty content → None)."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        empty_file = inbox_dir / "empty.md"
        empty_file.write_text("", encoding="utf-8")

        t = LocalTransport(str(inbox_dir), str(outbox_dir))

        assert t.read_file("empty.md") is None


class TestLocalTransportPing:
    """Test ping() method for inbox existence check."""

    def test_ping_inbox_exists(self, tmp_path):
        """ping() should return True when inbox exists."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        t = LocalTransport(str(inbox_dir), str(outbox_dir))
        assert t.ping() is True

    def test_ping_inbox_missing(self, tmp_path):
        """ping() should return False when inbox does not exist."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        outbox_dir.mkdir()
        # inbox_dir intentionally not created

        t = LocalTransport(str(inbox_dir), str(outbox_dir))
        assert t.ping() is False


class TestLocalTransportSendReply:
    """Test send_reply behavior with various content."""

    def test_send_reply_creates_outbox_if_missing(self, tmp_path):
        """send_reply should create outbox directory if it does not exist."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        # outbox_dir intentionally not created

        t = LocalTransport(str(inbox_dir), str(outbox_dir))
        result = t.send_reply("content", "", "reply.md")

        assert result is True
        assert outbox_dir.exists()
        assert (outbox_dir / "reply.md").read_text(encoding="utf-8") == "content"

    def test_send_reply_ignores_outbox_remote_param(self, tmp_path):
        """send_reply should ignore _outbox_remote parameter and use self.outbox."""
        inbox_dir = tmp_path / "inbox"
        outbox_dir = tmp_path / "outbox"
        inbox_dir.mkdir()
        outbox_dir.mkdir()

        t = LocalTransport(str(inbox_dir), str(outbox_dir))

        # _outbox_remote is ignored; file should go to self.outbox
        result = t.send_reply("reply", "ignored/path/", "out.md")

        assert result is True
        assert (outbox_dir / "out.md").read_text(encoding="utf-8") == "reply"
        # File should NOT be created at ignored/path/
        assert not (outbox_dir / "ignored" / "path" / "out.md").exists()
