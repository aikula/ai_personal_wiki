"""
dependencies.py — FastAPI dependency injection.

All routes receive pre-built agent instances via Depends().
Settings and agents are created once at startup and reused.
Chat sessions are stored in-memory (dict) — sufficient for Phase 1.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agents.audit_agent import AuditAgent
from app.agents.ingest_agent import IngestAgent
from app.agents.query_agent import ChatSession, QueryAgent
from app.config import Settings
from app.core.context import WorkspaceContext, personal_local_context, personal_server_context
from app.core.interpreter import CodeInterpreter
from app.core.llm_client import LLMClient
from app.core.wiki_fs import WikiFS

security = HTTPBearer(auto_error=False)

# ── Singleton settings ───────────────────────────────────────────

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load("config/settings.yaml")


def _get_control_store(settings: Settings):
    """Resolve ControlStore based on app mode."""
    if settings.app_mode == "multi_user":
        from app.core.control_store import SQLiteControlStore
        from app.core.migrations.runner import run_migrations
        db_url = settings.control.db_url
        if db_url.startswith("sqlite:///"):
            db_path = Path(db_url[len("sqlite:///"):])
        else:
            db_path = Path(db_url)
        run_migrations(db_path)
        return SQLiteControlStore(db_path)
    from app.core.control_store import NoopControlStore
    return NoopControlStore()


# ── Workspace context ────────────────────────────────────────────

def get_workspace_context(
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> WorkspaceContext:
    """Resolve workspace context based on app mode.

    Personal modes: return a fixed local context.
    Multi-user: resolve from current user's workspace.
    """
    wiki_path = settings.wiki_data_path

    if settings.app_mode == "personal_server":
        return personal_server_context(wiki_path)
    if settings.app_mode != "multi_user":
        return personal_local_context(wiki_path)

    # Multi-user: resolve workspace from session token
    store = _get_control_store(settings)
    token = credentials.credentials if credentials else None

    if token:
        user = store.get_user_by_session_token(token)
        if user:
            ws = store.get_default_workspace(user.id)
            if ws:
                return WorkspaceContext(
                    workspace_id=ws.id,
                    owner_user_id=user.id,
                    mode="multi_user",
                    wiki_data_path=Path(ws.root_path),
                    quota_subject_id=user.id,
                )

    # Fallback: use settings path (will fail auth later if required)
    return WorkspaceContext(
        workspace_id="unknown",
        owner_user_id=None,
        mode="multi_user",
        wiki_data_path=Path(wiki_path),
        quota_subject_id=None,
    )


# ── Per-request dependencies ─────────────────────────────────────

def get_wiki_fs(
    ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WikiFS:
    return WikiFS.from_path(ctx.wiki_data_path, limits=settings.limits)


def get_llm_client(settings: Annotated[Settings, Depends(get_settings)]) -> LLMClient:
    return LLMClient(settings)


def get_interpreter(
    ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CodeInterpreter:
    return CodeInterpreter(wiki_root=ctx.wiki_data_path / "wiki")


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
