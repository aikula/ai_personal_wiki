"""
auth.py — Account authentication routes for multi-user mode.

POST /api/auth/register
POST /api/auth/login
POST /api/auth/logout
GET  /api/auth/me
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.api.dependencies import get_settings, get_workspace_context
from app.api.models import (
    AuthMeResponse,
    CreditOut,
    LoginRequest,
    LoginResponse,
    LogoutResponse,
    RegisterRequest,
    RegisterResponse,
    UserOut,
    WorkspaceOut,
)
from app.config import Settings
from app.core.context import WorkspaceContext
from app.core.control_store import (
    ControlStore,
    NoopControlStore,
    SQLiteControlStore,
)
from app.core.migrations.runner import run_migrations

logger = logging.getLogger("wiki.api.auth")
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _hash_password(password: str) -> str:
    """Hash password using SHA-256 with salt.

    For MVP: SHA-256 with token salt. In production, use argon2 or bcrypt.
    """
    salt = "wiki-engine-mvp-salt"
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def _get_control_store(settings: Settings) -> ControlStore:
    """Resolve ControlStore based on app mode. Runs migrations on first use."""
    if settings.app_mode == "multi_user":
        db_url = settings.control.db_url
        if db_url.startswith("sqlite:///"):
            db_path = Path(db_url[len("sqlite:///"):])
        else:
            db_path = Path(db_url)
        run_migrations(db_path)
        return SQLiteControlStore(db_path)
    return NoopControlStore()


security = HTTPBearer(auto_error=False)


def get_current_user(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    """Extract current user from bearer token.

    For personal modes: returns a synthetic user.
    For multi-user: validates token against ControlStore.
    """
    store = _get_control_store(settings)

    if settings.app_mode != "multi_user":
        return {
            "user_id": "local",
            "email": "local@wiki-engine",
            "is_admin": True,
            "is_active": True,
        }

    if credentials is None:
        raise HTTPException(401, "Authentication required")

    token = credentials.credentials
    user = store.get_user_by_session_token(token)
    if user is None:
        raise HTTPException(401, "Invalid or expired token")
    if not user.is_active:
        raise HTTPException(403, "Account is deactivated")

    return {
        "user_id": user.id,
        "email": user.email,
        "is_admin": user.is_admin,
        "is_active": user.is_active,
    }


@router.post("/register")
async def register(
    body: RegisterRequest,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Create a new user account with default workspace and credit buckets."""
    if settings.app_mode != "multi_user":
        raise HTTPException(400, "Registration only available in multi-user mode")
    if not settings.multi_user.registration_enabled:
        raise HTTPException(403, "Registration is disabled")

    store = _get_control_store(settings)

    # Check if email already exists
    existing = store.get_user_by_email(body.email)
    if existing:
        raise HTTPException(409, "Email already registered")

    # Create user
    password_hash = _hash_password(body.password)
    user = store.create_user(body.email, password_hash)

    # Create default workspace
    slug = body.email.split("@")[0].lower().replace(".", "-")[:32]
    workspaces_root = Path(settings.control.workspaces_root)
    workspace_root = workspaces_root / user.id

    ws = store.create_default_workspace(
        user_id=user.id,
        name=f"{body.email}'s Wiki",
        slug=slug,
        root_path=str(workspace_root),
    )

    # Create filesystem skeleton
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "raw" / "_general").mkdir(parents=True, exist_ok=True)
    (workspace_root / "wiki").mkdir(parents=True, exist_ok=True)

    # Create credit buckets
    store.create_credit_buckets(
        user_id=user.id,
        daily_limit=settings.multi_user.default_daily_tokens,
        welcome_limit=settings.multi_user.default_welcome_tokens,
    )

    # Create session
    token = store.create_session(user.id)

    return RegisterResponse(
        user=UserOut(
            id=user.id, email=user.email,
            is_admin=user.is_admin, is_active=user.is_active,
        ),
        token=token,
        workspace=WorkspaceOut(id=ws.id, name=ws.name, slug=ws.slug),
    )


@router.post("/login")
async def login(
    body: LoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Authenticate with email and password, receive bearer token."""
    if settings.app_mode != "multi_user":
        raise HTTPException(400, "Login only available in multi-user mode")

    store = _get_control_store(settings)
    password_hash = _hash_password(body.password)
    user = store.verify_password(body.email, password_hash)

    if user is None:
        raise HTTPException(401, "Invalid email or password")

    token = store.create_session(user.id)

    return LoginResponse(
        user=UserOut(
            id=user.id, email=user.email,
            is_admin=user.is_admin, is_active=user.is_active,
        ),
        token=token,
    )


@router.post("/logout")
async def logout(
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
):
    """Revoke current session token."""
    if settings.app_mode != "multi_user":
        return LogoutResponse(ok=True)

    if credentials is None:
        raise HTTPException(400, "Token required")

    store = _get_control_store(settings)
    store.revoke_session(credentials.credentials)

    return LogoutResponse(ok=True)


@router.get("/me")
async def auth_me(
    settings: Annotated[Settings, Depends(get_settings)],
    ctx: Annotated[WorkspaceContext, Depends(get_workspace_context)],
    user: Annotated[dict, Depends(get_current_user)],
):
    """Get current user info, workspace, and credit state."""
    if settings.app_mode != "multi_user":
        return AuthMeResponse(
            user=UserOut(id="local", email="local@wiki-engine", is_admin=True, is_active=True),
            workspace=None,
            credits=None,
        )

    store = _get_control_store(settings)
    ws = store.get_default_workspace(user["user_id"])
    credit_state = store.get_credit_state(user["user_id"])

    return AuthMeResponse(
        user=UserOut(
            id=user["user_id"], email=user["email"],
            is_admin=user["is_admin"], is_active=user["is_active"],
        ),
        workspace=WorkspaceOut(id=ws.id, name=ws.name, slug=ws.slug) if ws else None,
        credits=CreditOut(
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
        ),
    )
