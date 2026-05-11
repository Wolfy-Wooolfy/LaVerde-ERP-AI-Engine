"""Pydantic schemas for the AI chat assistant."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class ChatMessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ChatMessage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    role: ChatMessageRole
    content: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    data_snapshot: dict | None = None
    intent: str | None = None
    cost_usd: float = 0.0


class ChatSession(BaseModel):
    session_id: str
    messages: list[ChatMessage] = Field(default_factory=list)
    locale: str = "en"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_activity: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_message_count: int = 0


class QueryIntent(BaseModel):
    intent: str
    filters: dict = Field(default_factory=dict)
    response_format: Literal["table", "number", "list", "analysis", "mini_dashboard"] = "analysis"
    confidence: float = 0.8


class ChatRequest(BaseModel):
    session_id: str
    message: str = Field(min_length=1, max_length=500)


class ChatResponse(BaseModel):
    session_id: str
    message: ChatMessage
    suggested_followups: list[str] = Field(default_factory=list)
