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

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.agents.audit_agent import AuditAgent
from app.agents.ingest_agent import IngestAgent
from app.agents.query_agent import ChatSession, QueryAgent
from app.api.session_store import SessionStore
from app.config import Settings
from app.core.context import WorkspaceContext, personal_local_context, personal_server_context
from app.core.interpreter import CodeInterpreter
from app.core.llm_client import LLMClient, LLMGateway
from app.core.metered_llm_client import MeteredLLMClient
from app.core.wiki_fs import WikiFS

security = HTTPBearer(auto_error=False)

# ── Singleton settings ───────────────────────────────────────────

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.load("config/settings.yaml")


def build_control_store(settings: Settings):
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


def build_base_llm_client(settings: Settings) -> LLMClient:
    return LLMClient(settings)


# ── Workspace context ────────────────────────────────────────────

def get_workspace_context(
    settings: Annotated[Settings, Depends(get_settings)],
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
    access_token: str | None = Query(default=None),
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
    store = build_control_store(settings)
    token = credentials.credentials if credentials else access_token

    if not token:
        raise HTTPException(401, "Authentication required")

    user = store.get_user_by_session_token(token)
    if user is None:
        raise HTTPException(401, "Invalid or expired token")

    ws = store.get_default_workspace(user.id)
    if ws is None:
        raise HTTPException(403, "Workspace is not configured for this account")

    return WorkspaceContext(
        workspace_id=ws.id,
        owner_user_id=user.id,
        mode="multi_user",
        wiki_data_path=Path(ws.root_path),
        quota_subject_id=user.id,
    )


# ── Per-request dependencies ─────────────────────────────────────

def get_wiki_fs(
    ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> WikiFS:
    return WikiFS.from_path(ctx.wiki_data_path, limits=settings.limits)


def get_llm_client(
    settings: Annotated[Settings, Depends(get_settings)],
    ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)],
):
    base_client = build_base_llm_client(settings)
    if settings.app_mode != "multi_user":
        return base_client
    return MeteredLLMClient(
        llm_client=base_client,
        settings=settings,
        control_store=build_control_store(settings),
        user_id=ctx.quota_subject_id,
        workspace_id=ctx.workspace_id,
    )


def get_interpreter(
    ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> CodeInterpreter:
    return CodeInterpreter(wiki_root=ctx.wiki_data_path / "wiki")


def get_ingest_agent(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    llm: Annotated[LLMGateway, Depends(get_llm_client)],
    interpreter: Annotated[CodeInterpreter, Depends(get_interpreter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> IngestAgent:
    return IngestAgent(fs, llm, interpreter, settings)


def get_query_agent(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    llm: Annotated[LLMGateway, Depends(get_llm_client)],
    interpreter: Annotated[CodeInterpreter, Depends(get_interpreter)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QueryAgent:
    return QueryAgent(fs, llm, interpreter, settings)


def get_audit_agent(
    fs: Annotated[WikiFS, Depends(get_wiki_fs)],
    llm: Annotated[LLMGateway, Depends(get_llm_client)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AuditAgent:
    return AuditAgent(fs, llm, settings)


# ── Session store (persistent) ──────────────────────────────────
# Sessions persist to wiki-data/sessions/<scope>.json

_session_stores: dict[str, SessionStore] = {}


def get_session_store(
    ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)],
) -> dict[str, ChatSession]:
    scope = ctx.owner_user_id or ctx.workspace_id or "local"
    if scope not in _session_stores:
        sessions_dir = ctx.wiki_data_path / "sessions"
        json_path = sessions_dir / f"{scope}.json"
        _session_stores[scope] = SessionStore(json_path)
    return _session_stores[scope]


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
