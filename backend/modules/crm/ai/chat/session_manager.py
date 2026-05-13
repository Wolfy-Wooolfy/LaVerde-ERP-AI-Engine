"""In-memory chat session storage with TTL cleanup."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from loguru import logger

from backend.modules.crm.ai.chat.schemas import ChatMessage, ChatSession


class SessionManager:
    """Thread-safe in-memory session store. Not persisted — clears on restart."""

    MAX_SESSION_MESSAGES = 50  # hard lifetime cap per session

    def __init__(self, max_context_messages: int = 20, ttl_hours: int = 24) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._max_context = max_context_messages
        self._ttl = timedelta(hours=ttl_hours)
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str, locale: str) -> ChatSession:
        async with self._lock:
            if session_id not in self._sessions:
                now = datetime.now(timezone.utc)
                self._sessions[session_id] = ChatSession(
                    session_id=session_id,
                    locale=locale,
                    created_at=now,
                    last_activity=now,
                )
            return self._sessions[session_id]

    async def add_message(self, session_id: str, message: ChatMessage) -> None:
        async with self._lock:
            if session_id not in self._sessions:
                return
            session = self._sessions[session_id]
            session.messages.append(message)
            session.total_message_count += 1
            if len(session.messages) > self._max_context:
                session.messages = session.messages[-self._max_context:]
            session.last_activity = datetime.now(timezone.utc)

    def is_session_full(self, session: ChatSession) -> bool:
        return session.total_message_count >= self.MAX_SESSION_MESSAGES

    async def get_recent_context(self, session_id: str, n: int = 20) -> list[ChatMessage]:
        async with self._lock:
            if session_id not in self._sessions:
                return []
            return list(self._sessions[session_id].messages[-n:])

    async def delete_session(self, session_id: str) -> bool:
        async with self._lock:
            existed = session_id in self._sessions
            self._sessions.pop(session_id, None)
            return existed

    async def cleanup_expired(self) -> int:
        now = datetime.now(timezone.utc)
        async with self._lock:
            expired = [
                sid for sid, s in self._sessions.items()
                if (now - s.last_activity) >= self._ttl
            ]
            for sid in expired:
                del self._sessions[sid]
        if expired:
            logger.debug(f"Chat session cleanup: removed {len(expired)} expired sessions")
        return len(expired)

    @property
    def session_count(self) -> int:
        return len(self._sessions)
