"""
control_store_sqlite.py — SQLite-backed ControlStore implementation.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import bcrypt

from app.core.control_store import (
    CreditState,
    InsufficientCreditsError,
    UsageEvent,
    UserRecord,
    WorkspaceRecord,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger("wiki.control")


class SQLiteControlStore:
    """ControlStore backed by SQLite with WAL mode and foreign keys."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def get_user_by_email(self, email: str) -> UserRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.lower(),)
            ).fetchone()
            if row is None:
                return None
            return _row_to_user(row)
        finally:
            conn.close()

    def set_user_admin(self, user_id: str, is_admin: bool) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE users SET is_admin = ?, updated_at = ? WHERE id = ?",
                (1 if is_admin else 0, now, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def create_user(self, email: str, password_hash: str) -> UserRecord:
        now = datetime.now(timezone.utc).isoformat()
        user_id = secrets.token_hex(16)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO users (id, email, password_hash, is_active, is_admin, created_at, updated_at) "
                "VALUES (?, ?, ?, 1, 0, ?, ?)",
                (user_id, email.lower(), password_hash, now, now),
            )
            conn.commit()
            return UserRecord(
                id=user_id, email=email.lower(), is_active=True,
                is_admin=False, created_at=now, updated_at=now,
            )
        finally:
            conn.close()

    def verify_password(self, email: str, password_hash: str) -> UserRecord | None:
        user = self.get_user_by_email(email)
        if user is None or not self.password_matches(user.id, password_hash):
            return None
        self._touch_last_login(user.id)
        return user

    def password_matches(self, user_id: str, password: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT password_hash FROM users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
            if row is None:
                return False
            try:
                return bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8"))
            except ValueError:
                logger.warning("Invalid password hash format for user_id=%s", user_id)
                return False
        finally:
            conn.close()

    def create_session(self, user_id: str) -> str:
        raw_token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        expires = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO sessions (id, user_id, token_hash, expires_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (secrets.token_hex(16), user_id, token_hash, expires, now),
            )
            conn.commit()
            return raw_token
        finally:
            conn.close()

    def get_user_by_session_token(self, token: str) -> UserRecord | None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT u.*, s.expires_at FROM users u "
                "JOIN sessions s ON s.user_id = u.id "
                "WHERE s.token_hash = ? AND s.revoked_at IS NULL",
                (token_hash,),
            ).fetchone()
            if row is None:
                return None
            expires_at = row["expires_at"]
            if expires_at and _parse_timestamp(expires_at) <= datetime.now(timezone.utc):
                return None
            return _row_to_user(row)
        finally:
            conn.close()

    def revoke_session(self, token: str) -> None:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
                (now, token_hash),
            )
            conn.commit()
        finally:
            conn.close()

    def get_default_workspace(self, user_id: str) -> WorkspaceRecord | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM workspaces WHERE owner_user_id = ? ORDER BY created_at ASC LIMIT 1",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            return _row_to_workspace(row)
        finally:
            conn.close()

    def create_default_workspace(
        self, user_id: str, name: str, slug: str, root_path: str,
    ) -> WorkspaceRecord:
        now = datetime.now(timezone.utc).isoformat()
        ws_id = secrets.token_hex(16)
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO workspaces (id, owner_user_id, name, slug, root_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ws_id, user_id, name, slug, root_path, now, now),
            )
            conn.commit()
            return WorkspaceRecord(
                id=ws_id, owner_user_id=user_id, name=name,
                slug=slug, root_path=root_path, created_at=now, updated_at=now,
            )
        finally:
            conn.close()

    def reset_all_due_daily_buckets(self) -> int:
        now = datetime.now(timezone.utc)
        conn = self._connect()
        try:
            due = conn.execute(
                "SELECT id, tokens_used, reset_at FROM credit_buckets "
                "WHERE bucket_type = 'daily' AND reset_at IS NOT NULL",
            ).fetchall()
            count = 0
            for row in due:
                if _parse_timestamp(row["reset_at"]) <= now:
                    conn.execute(
                        "UPDATE credit_buckets SET tokens_used = 0, reset_at = ?, updated_at = ? WHERE id = ?",
                        (_next_reset_at(), now.isoformat(), row["id"]),
                    )
                    if row["tokens_used"] > 0:
                        count += 1
            conn.commit()
            if count > 0 or len(due) > 0:
                logger.info("Daily buckets: %d due, %d actually reset", len(due), count)
            return count
        finally:
            conn.close()

    def get_credit_state(self, user_id: str) -> CreditState:
        conn = self._connect()
        try:
            self._reset_due_daily_bucket(conn, user_id)
            rows = self._fetch_credit_bucket_rows(conn, user_id)
            return _credit_state_from_rows(rows)
        finally:
            conn.close()

    def consume_tokens(self, user_id: str, amount: int) -> CreditState:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._reset_due_daily_bucket(conn, user_id)
            rows = self._fetch_credit_bucket_rows(conn, user_id)
            state = _credit_state_from_rows(rows)
            total_remaining = state.daily_remaining + state.welcome_remaining
            if total_remaining < amount:
                conn.rollback()
                raise InsufficientCreditsError(
                    required=amount, available=total_remaining,
                )

            now = datetime.now(timezone.utc).isoformat()
            daily_spend = min(amount, state.daily_remaining)
            welcome_spend = amount - daily_spend

            if daily_spend > 0:
                conn.execute(
                    "UPDATE credit_buckets SET tokens_used = tokens_used + ?, updated_at = ? "
                    "WHERE user_id = ? AND bucket_type = 'daily'",
                    (daily_spend, now, user_id),
                )
            if welcome_spend > 0:
                conn.execute(
                    "UPDATE credit_buckets SET tokens_used = tokens_used + ?, updated_at = ? "
                    "WHERE user_id = ? AND bucket_type = 'welcome'",
                    (welcome_spend, now, user_id),
                )
            conn.commit()
            rows = self._fetch_credit_bucket_rows(conn, user_id)
            return _credit_state_from_rows(rows)
        finally:
            conn.close()

    def refund_tokens(self, user_id: str, amount: int) -> CreditState:
        if amount <= 0:
            return self.get_credit_state(user_id)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            self._reset_due_daily_bucket(conn, user_id)
            rows = self._fetch_credit_bucket_rows(conn, user_id)
            state = _credit_state_from_rows(rows)
            now = datetime.now(timezone.utc).isoformat()

            welcome_refund = min(amount, state.welcome_used)
            daily_refund = min(amount - welcome_refund, state.daily_used)

            if welcome_refund > 0:
                conn.execute(
                    "UPDATE credit_buckets SET tokens_used = tokens_used - ?, updated_at = ? "
                    "WHERE user_id = ? AND bucket_type = 'welcome'",
                    (welcome_refund, now, user_id),
                )
            if daily_refund > 0:
                conn.execute(
                    "UPDATE credit_buckets SET tokens_used = tokens_used - ?, updated_at = ? "
                    "WHERE user_id = ? AND bucket_type = 'daily'",
                    (daily_refund, now, user_id),
                )

            conn.commit()
            rows = self._fetch_credit_bucket_rows(conn, user_id)
            return _credit_state_from_rows(rows)
        finally:
            conn.close()

    def record_usage(self, event: UsageEvent) -> None:
        now = event.created_at or datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT INTO usage_events "
                "(id, user_id, workspace_id, operation, model, input_tokens, output_tokens, "
                "total_tokens, is_estimated, request_id, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    secrets.token_hex(16),
                    event.user_id,
                    event.workspace_id,
                    event.operation,
                    event.model,
                    event.input_tokens,
                    event.output_tokens,
                    event.total_tokens,
                    1 if event.is_estimated else 0,
                    event.request_id,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_usage(self, user_id: str, limit: int = 20) -> list[dict]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM usage_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def create_credit_buckets(self, user_id: str, daily_limit: int, welcome_limit: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO credit_buckets (id, user_id, bucket_type, token_limit, tokens_used, reset_at, expires_at, created_at, updated_at) "
                "VALUES (?, ?, 'daily', ?, 0, ?, NULL, ?, ?)",
                (secrets.token_hex(16), user_id, daily_limit, _next_reset_at(), now, now),
            )
            conn.execute(
                "INSERT OR IGNORE INTO credit_buckets (id, user_id, bucket_type, token_limit, tokens_used, reset_at, expires_at, created_at, updated_at) "
                "VALUES (?, ?, 'welcome', ?, 0, NULL, NULL, ?, ?)",
                (secrets.token_hex(16), user_id, welcome_limit, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def _touch_last_login(self, user_id: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, user_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _fetch_credit_bucket_rows(self, conn: sqlite3.Connection, user_id: str) -> list[sqlite3.Row]:
        return conn.execute(
            "SELECT * FROM credit_buckets WHERE user_id = ? AND bucket_type IN ('daily', 'welcome')",
            (user_id,),
        ).fetchall()

    def _reset_due_daily_bucket(self, conn: sqlite3.Connection, user_id: str) -> None:
        now = datetime.now(timezone.utc)
        row = conn.execute(
            "SELECT id, reset_at, tokens_used FROM credit_buckets WHERE user_id = ? AND bucket_type = 'daily'",
            (user_id,),
        ).fetchone()
        if row is None or not row["reset_at"]:
            return
        if _parse_timestamp(row["reset_at"]) > now:
            return
        if row["tokens_used"] == 0:
            conn.execute(
                "UPDATE credit_buckets SET reset_at = ?, updated_at = ? WHERE id = ?",
                (_next_reset_at(), now.isoformat(), row["id"]),
            )
            return
        conn.execute(
            "UPDATE credit_buckets SET tokens_used = 0, reset_at = ?, updated_at = ? WHERE id = ?",
            (_next_reset_at(), now.isoformat(), row["id"]),
        )


# ── Helpers ───────────────────────────────────────────────────────

def _row_to_user(row: sqlite3.Row) -> UserRecord:
    return UserRecord(
        id=row["id"],
        email=row["email"],
        is_active=bool(row["is_active"]),
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_login_at=row["last_login_at"],
    )


def _row_to_workspace(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        owner_user_id=row["owner_user_id"],
        name=row["name"],
        slug=row["slug"],
        root_path=row["root_path"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _credit_state_from_rows(rows: list[sqlite3.Row]) -> CreditState:
    daily_limit = 0
    daily_used = 0
    daily_reset_at = None
    welcome_limit = 0
    welcome_used = 0

    for row in rows:
        if row["bucket_type"] == "daily":
            daily_limit = row["token_limit"]
            daily_used = row["tokens_used"]
            daily_reset_at = row["reset_at"]
        elif row["bucket_type"] == "welcome":
            welcome_limit = row["token_limit"]
            welcome_used = row["tokens_used"]

    return CreditState(
        daily_limit=daily_limit,
        daily_used=daily_used,
        daily_remaining=max(0, daily_limit - daily_used),
        daily_reset_at=daily_reset_at,
        welcome_limit=welcome_limit,
        welcome_used=welcome_used,
        welcome_remaining=max(0, welcome_limit - welcome_used),
    )


def _parse_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _next_reset_at() -> str:
    now = datetime.now(timezone.utc)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow.isoformat()
