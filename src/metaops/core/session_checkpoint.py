"""Basic session checkpoint for crash recovery.

Provides lightweight persistence of in-flight turn state.  On crash,
the checkpoint can be materialized into session history so no tool
results are lost.

Follows the Nanobot pattern: one JSON file per session, overwritten
on each turn boundary.  Old checkpoints are cleaned up automatically.
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_CHECKPOINT_DIR = Path(os.getenv("METAOPS_CHECKPOINT_DIR", "~/.metaops/checkpoints")).expanduser()


def cleanup_stale_checkpoints() -> int:
    """Remove checkpoint files older than config.checkpoint_ttl_hours. Returns count removed."""
    if not _CHECKPOINT_DIR.exists():
        return 0
    from metaops.config import get_config
    max_age = get_config().checkpoint_ttl_hours * 3600
    now = time.time()
    removed = 0
    for f in _CHECKPOINT_DIR.glob("*.json"):
        try:
            if now - f.stat().st_mtime > max_age:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        logger.info("Cleaned up %d stale checkpoint(s)", removed)
    return removed


class SessionCheckpoint:
    """Per-session checkpoint stored as a JSON file.

    Checkpoint is written at each tool-call boundary so that crash
    recovery can restore partial conversation state.
    """

    def __init__(self, session_key: str):
        """Initialize checkpoint for a given session.

        Args:
            session_key: Unique session identifier (e.g. "telegram:12345").
        """
        self.session_key = session_key
        self._safe_key = session_key.replace("/", "_").replace(":", "_")
        self._path = _CHECKPOINT_DIR / f"{self._safe_key}.json"
        self._data: dict[str, Any] = {}

    def exists(self) -> bool:
        """Check if a checkpoint file exists on disk."""
        return self._path.exists()

    def load(self) -> Optional[dict[str, Any]]:
        """Load checkpoint from disk. Returns None if no checkpoint exists."""
        if not self._path.exists():
            return None
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            logger.debug("Loaded checkpoint for session %s", self.session_key)
            return self._data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load checkpoint for %s: %s", self.session_key, e)
            return None

    def save(self, data: dict[str, Any]) -> None:
        """Save checkpoint to disk (atomic write via temp file + replace)."""
        self._data = data
        self._data["_checkpoint_ts"] = time.time()
        self._data["_session_key"] = self.session_key
        _CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, self._path)
            logger.debug("Saved checkpoint for session %s", self.session_key)
        except OSError as e:
            logger.warning("Failed to save checkpoint for %s: %s", self.session_key, e)
        # Lazy cleanup: remove stale checkpoints from other sessions
        cleanup_stale_checkpoints()

    def clear(self) -> None:
        """Remove checkpoint file from disk."""
        try:
            if self._path.exists():
                self._path.unlink()
            self._data = {}
            logger.debug("Cleared checkpoint for session %s", self.session_key)
        except OSError as e:
            logger.warning("Failed to clear checkpoint for %s: %s", self.session_key, e)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from the checkpoint data."""
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a value in the checkpoint data (does not persist until save())."""
        self._data[key] = value
