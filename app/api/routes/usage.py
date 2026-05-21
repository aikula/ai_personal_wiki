"""
usage.py — Token usage and quota endpoints for multi-user mode.

GET /api/usage/me
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_settings
from app.api.models import UsageResponse
from app.config import Settings
from app.core.control_store import ControlStore, NoopControlStore, SQLiteControlStore
from app.core.migrations.runner import run_migrations

logger = logging.getLogger("wiki.api.usage")
router = APIRouter(prefix="/api/usage", tags=["usage"])
security = HTTPBearer(auto_error=False)


def _get_control_store(settings: Settings) -> ControlStore:
    if settings.app_mode == "multi_user":
        db_url = settings.control.db_url
        if db_url.startswith("sqlite:///"):
            db_path = Path(db_url[len("sqlite:///"):])
        else:
            db_path = Path(db_url)
        run_migrations(db_path)
        return SQLiteControlStore(db_path)
    return NoopControlStore()


@router.get("/me")
async def usage_me(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
):
    """Get current user's token usage and quota state."""
    if settings.app_mode != "multi_user":
        raise HTTPException(400, "Usage tracking only available in multi-user mode")

    if not credentials:
        raise HTTPException(401, "Authentication required")

    store = _get_control_store(settings)
    user = store.get_user_by_session_token(credentials.credentials)
    if user is None:
        raise HTTPException(401, "Invalid or expired token")

    credit_state = store.get_credit_state(user.id)
    recent = store.get_recent_usage(user.id, limit=20)

    return UsageResponse(
        daily={
            "limit": credit_state.daily_limit,
            "used": credit_state.daily_used,
            "remaining": credit_state.daily_remaining,
            "reset_at": credit_state.daily_reset_at,
        },
        welcome={
            "limit": credit_state.welcome_limit,
            "used": credit_state.welcome_used,
            "remaining": credit_state.welcome_remaining,
        },
        recent_events=recent,
    )
