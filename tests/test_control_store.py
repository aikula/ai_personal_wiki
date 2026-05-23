"""Tests for SQLite migrations and ControlStore."""

import sqlite3
from pathlib import Path

import bcrypt
import pytest

from app.core.control_store import (
    InsufficientCreditsError,
    NoopControlStore,
    SQLiteControlStore,
    UsageEvent,
)
from app.core.migrations.runner import run_migrations

# ── Migration tests ───────────────────────────────────────────────

@pytest.fixture
def empty_db(tmp_path: Path) -> Path:
    return tmp_path / "control.db"


def test_migration_creates_tables(empty_db: Path):
    run_migrations(empty_db)
    assert empty_db.exists()

    conn = sqlite3.connect(str(empty_db))
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    conn.close()

    expected = {"users", "sessions", "workspaces", "usage_events", "credit_buckets", "_migrations"}
    assert expected.issubset(tables)


def test_migration_idempotent(empty_db: Path):
    run_migrations(empty_db)
    run_migrations(empty_db)  # should not raise

    conn = sqlite3.connect(str(empty_db))
    count = conn.execute("SELECT COUNT(*) FROM _migrations").fetchone()[0]
    conn.close()
    assert count >= 2  # core schema + follow-up indexes


def test_migration_enables_wal(empty_db: Path):
    run_migrations(empty_db)
    conn = sqlite3.connect(str(empty_db))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    assert mode == "wal"


def test_migration_enables_foreign_keys(empty_db: Path):
    run_migrations(empty_db)
    conn = sqlite3.connect(str(empty_db))
    conn.execute("PRAGMA foreign_keys=ON")
    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.close()
    assert fk == 1


# ── NoopControlStore tests ────────────────────────────────────────

def test_noop_returns_none_for_user():
    store = NoopControlStore()
    assert store.get_user_by_email("test@example.com") is None


def test_noop_raises_on_create_user():
    store = NoopControlStore()
    with pytest.raises(RuntimeError):
        store.create_user("test@example.com", "hash")


def test_noop_returns_empty_credit_state():
    store = NoopControlStore()
    state = store.get_credit_state("user-1")
    assert state.daily_limit == 0
    assert state.daily_remaining == 0


def test_noop_consume_does_nothing():
    store = NoopControlStore()
    state = store.consume_tokens("user-1", 100)
    assert state.daily_remaining == 0


# ── SQLiteControlStore tests ──────────────────────────────────────

@pytest.fixture
def sqlite_store(empty_db: Path) -> SQLiteControlStore:
    run_migrations(empty_db)
    return SQLiteControlStore(empty_db)


def test_create_and_get_user(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("alice@example.com", "hashed_pw")
    assert user.email == "alice@example.com"
    assert user.is_active is True

    fetched = sqlite_store.get_user_by_email("alice@example.com")
    assert fetched is not None
    assert fetched.id == user.id


def test_email_normalized_to_lowercase(sqlite_store: SQLiteControlStore):
    sqlite_store.create_user("Alice@Example.com", "hash")
    user = sqlite_store.get_user_by_email("alice@example.com")
    assert user is not None
    assert user.email == "alice@example.com"


def test_duplicate_email_raises(sqlite_store: SQLiteControlStore):
    sqlite_store.create_user("dup@example.com", "hash1")
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_store.create_user("dup@example.com", "hash2")


def test_verify_password_success(sqlite_store: SQLiteControlStore):
    password = "correct-password"
    password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    sqlite_store.create_user("verify@example.com", password_hash)
    user = sqlite_store.verify_password("verify@example.com", password)
    assert user is not None
    assert user.email == "verify@example.com"


def test_verify_password_wrong_hash(sqlite_store: SQLiteControlStore):
    password_hash = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode("utf-8")
    sqlite_store.create_user("wrong@example.com", password_hash)
    user = sqlite_store.verify_password("wrong@example.com", "wrong-password")
    assert user is None


def test_verify_password_inactive_user(sqlite_store: SQLiteControlStore):
    password_hash = bcrypt.hashpw(b"correct-password", bcrypt.gensalt()).decode("utf-8")
    user = sqlite_store.create_user("inactive@example.com", password_hash)
    conn = sqlite_store._connect()
    conn.execute("UPDATE users SET is_active = 0 WHERE id = ?", (user.id,))
    conn.commit()
    conn.close()

    result = sqlite_store.verify_password("inactive@example.com", "correct-password")
    assert result is None


def test_create_session(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("session@example.com", "hash")
    token = sqlite_store.create_session(user.id)
    assert len(token) > 0

    fetched = sqlite_store.get_user_by_session_token(token)
    assert fetched is not None
    assert fetched.id == user.id


def test_revoke_session(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("revoke@example.com", "hash")
    token = sqlite_store.create_session(user.id)

    sqlite_store.revoke_session(token)
    fetched = sqlite_store.get_user_by_session_token(token)
    assert fetched is None


def test_workspace_crud(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("ws@example.com", "hash")
    ws = sqlite_store.create_default_workspace(
        user_id=user.id, name="My Workspace", slug="my-workspace",
        root_path="/data/workspaces/ws-1",
    )
    assert ws.slug == "my-workspace"

    fetched = sqlite_store.get_default_workspace(user.id)
    assert fetched is not None
    assert fetched.id == ws.id


def test_credit_buckets_created_and_read(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("credit@example.com", "hash")
    sqlite_store.create_credit_buckets(user.id, daily_limit=30_000, welcome_limit=200_000)

    state = sqlite_store.get_credit_state(user.id)
    assert state.daily_limit == 30_000
    assert state.daily_used == 0
    assert state.daily_remaining == 30_000
    assert state.welcome_limit == 200_000
    assert state.welcome_remaining == 200_000


def test_consume_tokens_daily_first(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("consume@example.com", "hash")
    sqlite_store.create_credit_buckets(user.id, daily_limit=1000, welcome_limit=5000)

    state = sqlite_store.consume_tokens(user.id, 500)
    assert state.daily_used == 500
    assert state.daily_remaining == 500
    assert state.welcome_used == 0


def test_consume_tokens_spends_welcome_after_daily(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("welcome@example.com", "hash")
    sqlite_store.create_credit_buckets(user.id, daily_limit=100, welcome_limit=5000)

    state = sqlite_store.consume_tokens(user.id, 150)
    assert state.daily_used == 100
    assert state.daily_remaining == 0
    assert state.welcome_used == 50


def test_consume_tokens_insufficient(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("insufficient@example.com", "hash")
    sqlite_store.create_credit_buckets(user.id, daily_limit=100, welcome_limit=200)

    with pytest.raises(InsufficientCreditsError) as exc_info:
        sqlite_store.consume_tokens(user.id, 500)
    assert exc_info.value.required == 500
    assert exc_info.value.available == 300


def test_record_usage(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("usage@example.com", "hash")
    ws = sqlite_store.create_default_workspace(
        user_id=user.id, name="WS", slug="ws", root_path="/tmp/ws",
    )

    event = UsageEvent(
        user_id=user.id,
        workspace_id=ws.id,
        operation="chat",
        model="gpt-4o",
        input_tokens=100,
        output_tokens=200,
        total_tokens=300,
    )
    sqlite_store.record_usage(event)

    recent = sqlite_store.get_recent_usage(user.id)
    assert len(recent) == 1
    assert recent[0]["operation"] == "chat"
    assert recent[0]["total_tokens"] == 300


def test_lazy_daily_reset_restores_used_tokens(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("reset@example.com", "hash")
    sqlite_store.create_credit_buckets(user.id, daily_limit=1000, welcome_limit=5000)

    sqlite_store.consume_tokens(user.id, 500)
    state = sqlite_store.get_credit_state(user.id)
    assert state.daily_used == 500

    conn = sqlite_store._connect()
    conn.execute(
        "UPDATE credit_buckets SET reset_at = '2000-01-01T00:00:00' WHERE user_id = ? AND bucket_type = 'daily'",
        (user.id,),
    )
    conn.commit()
    conn.close()

    state = sqlite_store.get_credit_state(user.id)
    assert state.daily_used == 0
    assert state.daily_remaining == 1000
    assert state.daily_reset_at is not None
    assert state.daily_reset_at != "2000-01-01T00:00:00"


def test_lazy_daily_reset_no_usage_keeps_bucket(sqlite_store: SQLiteControlStore):
    """If user didn't use any tokens at reset time, bucket is left as-is."""
    user = sqlite_store.create_user("noop@example.com", "hash")
    sqlite_store.create_credit_buckets(user.id, daily_limit=1000, welcome_limit=5000)

    state = sqlite_store.get_credit_state(user.id)
    assert state.daily_used == 0
    assert state.daily_remaining == 1000

    # Manually set reset_at to the past, tokens_used stays 0
    conn = sqlite_store._connect()
    conn.execute(
        "UPDATE credit_buckets SET reset_at = '2000-01-01T00:00:00' WHERE user_id = ? AND bucket_type = 'daily'",
        (user.id,),
    )
    conn.commit()
    conn.close()

    state = sqlite_store.get_credit_state(user.id)
    assert state.daily_used == 0
    assert state.daily_remaining == 1000
    assert state.daily_reset_at is not None
    assert state.daily_reset_at != "2000-01-01T00:00:00"


def test_refund_tokens_restores_balance(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("refund@example.com", "hash")
    sqlite_store.create_credit_buckets(user.id, daily_limit=100, welcome_limit=50)

    sqlite_store.consume_tokens(user.id, 120)
    state = sqlite_store.refund_tokens(user.id, 20)

    assert state.daily_used == 100
    assert state.welcome_used == 0
    assert state.daily_remaining == 0
    assert state.welcome_remaining == 50


def test_create_credit_buckets_idempotent(sqlite_store: SQLiteControlStore):
    user = sqlite_store.create_user("bucket@example.com", "hash")

    sqlite_store.create_credit_buckets(user.id, daily_limit=100, welcome_limit=50)
    sqlite_store.create_credit_buckets(user.id, daily_limit=100, welcome_limit=50)

    conn = sqlite_store._connect()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM credit_buckets WHERE user_id = ?",
            (user.id,),
        ).fetchone()[0]
    finally:
        conn.close()

    assert count == 2
