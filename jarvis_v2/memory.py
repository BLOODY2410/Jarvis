from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from jarvis_v2.models import GroundingSource, Intent, ToolResult


@dataclass(slots=True)
class SessionState:
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    messages: deque[dict[str, str]] = field(default_factory=deque)
    recent_tools: deque[ToolResult] = field(default_factory=lambda: deque(maxlen=20))
    grounded_facts: deque[dict[str, object]] = field(default_factory=lambda: deque(maxlen=20))
    last_entity: str | None = None
    current_topic: str | None = None
    last_app: str | None = None
    last_url: str | None = None
    last_screen: str | None = None
    last_tool: str | None = None
    recent_entities: deque[str] = field(default_factory=lambda: deque(maxlen=20))
    conversation_summary: str = ""
    pending_confirmation: Intent | None = None
    pending_confirmation_created_at: float = 0

    def add_message(self, role: str, content: str, max_turns: int) -> None:
        self.messages.append({"role": role, "content": content})
        while sum(item["role"] == "user" for item in self.messages) > max_turns:
            self.messages.popleft()
        if role == "user":
            self.current_topic = content
        if len(self.messages) >= max_turns * 2:
            recent = list(self.messages)[-4:]
            self.conversation_summary = " ".join(item["content"] for item in recent)


class MemoryStore:
    """SQLite-backed audit memory; secrets and audio are never persisted."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS session (
                session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT
            );
            CREATE TABLE IF NOT EXISTS conversation (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL,
                role TEXT NOT NULL, content TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS tool_event (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL,
                tool TEXT NOT NULL, success INTEGER NOT NULL, message TEXT NOT NULL, data_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS grounded_event (
                id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, created_at TEXT NOT NULL,
                query TEXT NOT NULL, answer TEXT NOT NULL, sources_json TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def start_session(self, session_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR IGNORE INTO session(session_id, started_at) VALUES (?, ?)",
                (session_id, self._now()),
            )
            self._connection.commit()

    def end_session(self, session_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "UPDATE session SET ended_at = ? WHERE session_id = ?",
                (self._now(), session_id),
            )
            self._connection.commit()

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    def message(self, session_id: str, role: str, content: str) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO conversation(session_id, created_at, role, content) VALUES (?, ?, ?, ?)",
                (session_id, self._now(), role, content),
            )
            self._connection.commit()

    def tool(self, session_id: str, result: ToolResult) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT INTO tool_event(session_id, created_at, tool, success, message, data_json) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    self._now(),
                    result.tool,
                    int(result.success),
                    result.message,
                    json.dumps(result.data, ensure_ascii=False),
                ),
            )
            self._connection.commit()

    def grounded(self, session_id: str, query: str, answer: str, sources: list[GroundingSource]) -> None:
        if not sources:
            raise ValueError("Grounded memory requires at least one provenance source")
        with self._lock:
            self._connection.execute(
                "INSERT INTO grounded_event(session_id, created_at, query, answer, sources_json) VALUES (?, ?, ?, ?, ?)",
                (
                    session_id,
                    self._now(),
                    query,
                    answer,
                    json.dumps([source.model_dump() for source in sources], ensure_ascii=False),
                ),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
