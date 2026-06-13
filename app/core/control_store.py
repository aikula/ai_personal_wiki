"""
control_store.py — ControlStore protocol and data records.

ControlStore is the interface for user/workspace/credit operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol


# ── Data records ──────────────────────────────────────────────────

@dataclass
class UserRecord:
    id: str
    email: str
    is_active: bool
    is_admin: bool
    created_at: str
    updated_at: str
    last_login_at: str | None = None


@dataclass
class WorkspaceRecord:
    id: str
    owner_user_id: str
    name: str
    slug: str
    root_path: str
    created_at: str
    updated_at: str


@dataclass
class CreditState:
    daily_limit: int
    daily_used: int
    daily_remaining: int
    daily_reset_at: str | None
    welcome_limit: int
    welcome_used: int
    welcome_remaining: int


@dataclass
class UsageEvent:
    user_id: str
    workspace_id: str
    operation: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    is_estimated: bool = True
    request_id: str | None = None
    created_at: str | None = None


# ── Interface ─────────────────────────────────────────────────────

class ControlStore(Protocol):
    def get_user_by_email(self, email: str) -> UserRecord | None: ...
    def create_user(self, email: str, password_hash: str) -> UserRecord: ...
    def verify_password(self, email: str, password_hash: str) -> UserRecord | None: ...
    def password_matches(self, user_id: str, password: str) -> bool: ...
    def set_user_admin(self, user_id: str, is_admin: bool) -> None: ...
    def create_session(self, user_id: str) -> str: ...
    def get_user_by_session_token(self, token: str) -> UserRecord | None: ...
    def revoke_session(self, token: str) -> None: ...
    def get_default_workspace(self, user_id: str) -> WorkspaceRecord | None: ...
    def create_default_workspace(self, user_id: str, name: str, slug: str, root_path: str) -> WorkspaceRecord: ...
    def get_credit_state(self, user_id: str) -> CreditState: ...
    def consume_tokens(self, user_id: str, amount: int) -> CreditState: ...
    def refund_tokens(self, user_id: str, amount: int) -> CreditState: ...
    def record_usage(self, event: UsageEvent) -> None: ...
    def get_recent_usage(self, user_id: str, limit: int = 20) -> list[dict]: ...


class InsufficientCreditsError(Exception):
    def __init__(self, required: int, available: int):
        self.required = required
        self.available = available
        super().__init__(f"Insufficient credits: need {required}, have {available}")
