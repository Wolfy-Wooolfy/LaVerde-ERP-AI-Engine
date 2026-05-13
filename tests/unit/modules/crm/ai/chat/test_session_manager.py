"""Unit tests for ChatSessionManager."""

from datetime import datetime, timezone

import pytest

from backend.modules.crm.ai.chat.schemas import ChatMessage, ChatMessageRole
from backend.modules.crm.ai.chat.session_manager import SessionManager


def _msg(content: str = "hello") -> ChatMessage:
    return ChatMessage(role=ChatMessageRole.USER, content=content)


async def test_create_session():
    mgr = SessionManager()
    session = await mgr.get_or_create("sid-1", "en")
    assert session.session_id == "sid-1"
    assert session.locale == "en"
    assert mgr.session_count == 1


async def test_get_returns_same_session():
    mgr = SessionManager()
    s1 = await mgr.get_or_create("sid-2", "en")
    s2 = await mgr.get_or_create("sid-2", "ar")  # locale change ignored for existing
    assert s1 is s2


async def test_add_message():
    mgr = SessionManager()
    await mgr.get_or_create("sid-3", "en")
    await mgr.add_message("sid-3", _msg("Hello"))
    ctx = await mgr.get_recent_context("sid-3")
    assert len(ctx) == 1
    assert ctx[0].content == "Hello"


async def test_context_window_caps_at_max():
    mgr = SessionManager(max_context_messages=3)
    await mgr.get_or_create("sid-4", "en")
    for i in range(5):
        await mgr.add_message("sid-4", _msg(f"msg{i}"))
    ctx = await mgr.get_recent_context("sid-4", n=10)
    assert len(ctx) == 3
    assert ctx[0].content == "msg2"  # oldest surviving message
    assert ctx[-1].content == "msg4"


async def test_total_message_count_tracks_lifetime():
    mgr = SessionManager(max_context_messages=3)
    await mgr.get_or_create("sid-5", "en")
    for _ in range(5):
        await mgr.add_message("sid-5", _msg())
    session = await mgr.get_or_create("sid-5", "en")
    assert session.total_message_count == 5


async def test_session_full_enforcement():
    mgr = SessionManager()
    mgr.MAX_SESSION_MESSAGES = 2
    session = await mgr.get_or_create("sid-6", "en")
    assert mgr.is_session_full(session) is False
    await mgr.add_message("sid-6", _msg())
    await mgr.add_message("sid-6", _msg())
    session = await mgr.get_or_create("sid-6", "en")
    assert mgr.is_session_full(session) is True


async def test_delete_session():
    mgr = SessionManager()
    await mgr.get_or_create("sid-7", "en")
    deleted = await mgr.delete_session("sid-7")
    assert deleted is True
    assert mgr.session_count == 0
    ctx = await mgr.get_recent_context("sid-7")
    assert ctx == []


async def test_delete_nonexistent_session():
    mgr = SessionManager()
    deleted = await mgr.delete_session("does-not-exist")
    assert deleted is False


async def test_cleanup_expired():
    mgr = SessionManager(ttl_hours=1)
    await mgr.get_or_create("sid-8", "en")
    # Force last_activity to past
    mgr._sessions["sid-8"].last_activity = datetime(2020, 1, 1, tzinfo=timezone.utc)
    count = await mgr.cleanup_expired()
    assert count == 1
    assert mgr.session_count == 0


async def test_cleanup_keeps_active_sessions():
    mgr = SessionManager(ttl_hours=24)
    await mgr.get_or_create("sid-9", "en")  # recent — should survive
    count = await mgr.cleanup_expired()
    assert count == 0
    assert mgr.session_count == 1


async def test_add_message_unknown_session_is_noop():
    mgr = SessionManager()
    await mgr.add_message("nonexistent", _msg())  # should not raise
    assert mgr.session_count == 0
