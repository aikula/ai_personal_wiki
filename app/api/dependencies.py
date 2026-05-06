"""
dependencies.py — FastAPI dependency injection.

All routes receive pre-built agent instances via Depends().
Settings and agents are created once at startup and reused.
Chat sessions are stored in-memory (dict) — sufficient for Phase 1.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.agents.audit_agent import AuditAgent
from app.agents.ingest_agent import IngestAgent
from app.agents.query_agent import ChatSession, QueryAgent
from app.config import Settings
from app.core.interpreter import CodeInterpreter
from app.core.llm_client import LLMClient
from app.core.wiki_fs import WikiFS

# ── Singleton settings ───────────────────────────────────────────

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load("config/settings.yaml")


# ── Per-request dependencies ─────────────────────────────────────

def get_wiki_fs(settings: Annotated[Settings, Depends(get_settings)]) -> WikiFS:
    return WikiFS(settings)


def get_llm_client(settings: Annotated[Settings, Depends(get_settings)]) -> LLMClient:
    return LLMClient(settings)


def get_interpreter(settings: Annotated[Settings, Depends(get_settings)]) -> CodeInterpreter:
    return CodeInterpreter(wiki_root=WikiFS(settings).wiki_dir)


def get_ingest_agent(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    interpreter: Annotated[CodeInterpreter, Depends(get_interpreter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IngestAgent:
    return IngestAgent(fs, llm, interpreter, settings)


def get_query_agent(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    interpreter: Annotated[CodeInterpreter, Depends(get_interpreter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueryAgent:
    return QueryAgent(fs, llm, interpreter, settings)


def get_audit_agent(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuditAgent:
    return AuditAgent(fs, llm, settings)


# ── In-memory session store ──────────────────────────────────────
# Phase 1: sessions live in RAM. Lost on restart.
# Phase 2: persist to wiki-data/sessions/ as JSON files.

_sessions: dict[str, ChatSession] = {}


def get_session_store() -> dict[str, ChatSession]:
    return _sessions


def get_or_create_session(
    session_id: str,
    project_filter: str | None,
    store: dict[str, ChatSession],
) -> ChatSession:
    if session_id not in store:
        from datetime import datetime
        store[session_id] = ChatSession(
            session_id=session_id,
            created_at=datetime.now().isoformat(timespec="seconds"),
            project_filter=project_filter,
        )
    return store[session_id]