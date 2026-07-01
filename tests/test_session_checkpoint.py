"""Tests for metaops.core.session_checkpoint module."""

import json
import os
import tempfile
from pathlib import Path

import pytest
from metaops.core.session_checkpoint import SessionCheckpoint, _CHECKPOINT_DIR


@pytest.fixture
def temp_checkpoint_dir(monkeypatch):
    """Override checkpoint dir to a temp directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(
            "metaops.core.session_checkpoint._CHECKPOINT_DIR",
            Path(tmpdir),
        )
        yield Path(tmpdir)


class TestSessionCheckpoint:
    def test_create_checkpoint(self, temp_checkpoint_dir):
        cp = SessionCheckpoint("test:session")
        assert cp.session_key == "test:session"
        assert cp.exists() is False

    def test_save_and_load(self, temp_checkpoint_dir):
        cp = SessionCheckpoint("test:session")
        data = {"messages": ["hello", "world"], "step": 3}
        cp.save(data)

        assert cp.exists() is True
        loaded = cp.load()
        assert loaded is not None
        assert loaded["messages"] == ["hello", "world"]
        assert loaded["step"] == 3
        assert "_checkpoint_ts" in loaded
        assert loaded["_session_key"] == "test:session"

    def test_load_nonexistent(self, temp_checkpoint_dir):
        cp = SessionCheckpoint("nonexistent")
        assert cp.load() is None

    def test_clear(self, temp_checkpoint_dir):
        cp = SessionCheckpoint("test:session")
        cp.save({"data": "value"})
        assert cp.exists() is True

        cp.clear()
        assert cp.exists() is False

    def test_get_set(self, temp_checkpoint_dir):
        cp = SessionCheckpoint("test:session")
        cp.set("key1", "value1")
        cp.set("key2", 42)

        assert cp.get("key1") == "value1"
        assert cp.get("key2") == 42
        assert cp.get("missing", "default") == "default"

    def test_atomic_write(self, temp_checkpoint_dir):
        """Verify no .tmp file is left after save."""
        cp = SessionCheckpoint("test:atomic")
        cp.save({"data": "test"})
        tmp_files = list(temp_checkpoint_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_session_key_sanitized(self, temp_checkpoint_dir):
        cp = SessionCheckpoint("telegram:123456")
        assert cp._safe_key == "telegram_123456"
