"""
query_types.py — Typed schemas for the query pipeline.

All dataclasses used for chat sessions, messages, and query results.
"""

from __future__ import annotations

from dataclasses import dataclass, field

QUESTION_TYPES = {"factual", "comparison", "exploratory", "meta"}


@dataclass
class ChatMessage:
    role: str
    content: str
    timestamp: str = ""
    cited_slugs: list[str] = field(default_factory=list)


@dataclass
class ChatSession:
    session_id: str
    created_at: str
    messages: list[ChatMessage] = field(default_factory=list)
    project_filter: str | None = None

    def to_llm_messages(self, budget_chars: int) -> list[dict]:
        result = []
        total = 0
        for msg in reversed(self.messages):
            if msg.role == "tool":
                continue
            entry = {"role": msg.role, "content": msg.content}
            total += len(msg.content)
            if total > budget_chars:
                break
            result.insert(0, entry)
        return result


@dataclass
class QueryResult:
    answer: str
    cited_slugs: list[str]
    question_type: str
    pages_read: list[str]
    iterations: int
    session_id: str
    crystallized_slug: str | None = None
