"""
session_store.py — Persistent chat session storage.

Phase 2: sessions live in wiki-data/sessions/ as JSON files.
Each scope (user/workspace) gets one JSON file with all sessions.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from app.agents.query_types import ChatMessage, ChatSession

logger = logging.getLogger(__name__)


class SessionStore(dict):
    """Dict-like store that persists ChatSessions to a JSON file.

    Used as a drop-in replacement for the in-memory dict.
    Thread-safe via a lock on all mutations.
    """

    def __init__(self, json_path: Path):
        super().__init__()
        self._path = json_path
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            for sid, raw in data.items():
                messages = [
                    ChatMessage(**msg) for msg in raw.get("messages", [])
                ]
                self[sid] = ChatSession(
                    session_id=raw["session_id"],
                    created_at=raw["created_at"],
                    messages=messages,
                    project_filter=raw.get("project_filter"),
                )
            logger.info("Loaded %d sessions from %s", len(self), self._path)
        except Exception as exc:
            logger.warning("Failed to load sessions from %s: %s", self._path, exc)

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for sid, session in self.items():
                data[sid] = {
                    "session_id": session.session_id,
                    "created_at": session.created_at,
                    "project_filter": session.project_filter,
                    "messages": [
                        {
                            "role": msg.role,
                            "content": msg.content,
                            "timestamp": msg.timestamp,
                            "cited_slugs": msg.cited_slugs,
                        }
                        for msg in session.messages
                    ],
                }
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("Failed to save sessions to %s: %s", self._path, exc)

    def __setitem__(self, key, value):
        with self._lock:
            super().__setitem__(key, value)
            self._save()

    def __delitem__(self, key):
        with self._lock:
            super().__delitem__(key)
            self._save()

    def pop(self, key, *args):
        with self._lock:
            result = super().pop(key, *args)
            self._save()
            return result

    def setdefault(self, key, default=None):
        with self._lock:
            if key not in self:
                super().__setitem__(key, default)
                self._save()
            return super().__getitem__(key)
