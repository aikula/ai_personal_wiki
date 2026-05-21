"""
context.py — Runtime request context: AppMode, CurrentUser, WorkspaceContext.

These types bridge settings, auth, and workspace resolution.
Agents never see HTTP auth; they receive a pre-resolved WorkspaceContext.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AppMode = Literal["personal_local", "personal_server", "multi_user"]


@dataclass
class CurrentUser:
    user_id: str
    email: str
    is_admin: bool = False
    is_active: bool = True


@dataclass
class WorkspaceContext:
    workspace_id: str
    owner_user_id: str | None
    mode: AppMode
    wiki_data_path: Path
    quota_subject_id: str | None


def personal_local_context(wiki_data_path: str | Path) -> WorkspaceContext:
    """Create WorkspaceContext for personal_local mode."""
    return WorkspaceContext(
        workspace_id="local",
        owner_user_id=None,
        mode="personal_local",
        wiki_data_path=Path(wiki_data_path),
        quota_subject_id=None,
    )


def personal_server_context(wiki_data_path: str | Path) -> WorkspaceContext:
    """Create WorkspaceContext for personal_server mode."""
    return WorkspaceContext(
        workspace_id="local",
        owner_user_id=None,
        mode="personal_server",
        wiki_data_path=Path(wiki_data_path),
        quota_subject_id=None,
    )
